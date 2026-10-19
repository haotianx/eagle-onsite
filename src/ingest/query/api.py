"""/chat routes + SSE (Query-Design §13) and the query app's startup and shutdown.

    router      APIRouter(prefix="/chat") with every chat JSON and SSE route; the server includes
                it under "/api". Routes reach the services through request.app.state.query.
    lifespan    starts the query services on app.state and stops them; the server enters it
                inside its own lifespan."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field, StringConstraints

from ingest.config import Config
from ingest.query import QueryConfig, chat_db_path, chat_logs_dir, load_query_config
from ingest.query.agent import ChatBusy, ChatEvent, Generations, LibraryNotReady, answer_parts
from ingest.query.library import LibraryCache
from ingest.query.store import ChatRow, ChatStore, UploadRow, UploadStatus
from ingest.query.uploads import MAX_BYTES, UploadError, Uploads

log = logging.getLogger(__name__)

TERMINAL_EVENTS = frozenset({"done", "error"})


@dataclass(frozen=True)
class QueryServices:
    config: Config
    qconfig: QueryConfig
    store: ChatStore
    library: LibraryCache
    uploads: Uploads
    generations: Generations


# Models


class Region(BaseModel):
    page: int
    x0: float
    y0: float
    x1: float
    y1: float


class Passage(BaseModel):
    """A chunk returned by search_documents or read_document."""

    chunk_id: str
    doc_id: str
    seq: int
    text: str
    pages: list[int]
    regions: list[Region]
    section: list[str]
    filename: str | None
    job_number: str | None
    project: str | None
    score: float | None = None
    also_in: int = 0


class LibraryDocument(BaseModel):
    """A document returned by list_documents."""

    doc_id: str
    paths: list[str]
    filename: str | None
    job_number: str | None
    project: str | None
    subfolder: str | None
    pages: int | None
    upload: bool
    pending: bool


class SearchStep(BaseModel):
    round: int
    tool: str
    query: str | None
    scope: str | None
    passages: list[Passage | LibraryDocument]
    error: str | None


class Citation(BaseModel):
    n: int
    doc_id: str
    chunk_id: str | None
    filename: str | None
    pages: list[int]
    regions: list[Region]
    cited_text: str
    removed: bool


class Scope(BaseModel):
    doc_id: str
    filename: str | None


class UploadCard(BaseModel):
    id: str
    doc_id: str
    filename: str
    pages: int | None
    status: UploadStatus
    stage: str | None
    error: str | None
    removable: bool
    removed: bool
    job_number: str | None
    project: str | None


class TextPart(BaseModel):
    type: Literal["text"]
    text: str


class CitationPart(BaseModel):
    type: Literal["citation"]
    n: int


AnswerPart = Annotated[TextPart | CitationPart, Field(discriminator="type")]


class UserMessage(BaseModel):
    id: str
    role: Literal["user"]
    text: str
    scope: Scope | None
    created_at: str
    attachments: list[UploadCard]


class AssistantMessage(BaseModel):
    id: str
    role: Literal["assistant"]
    state: Literal["generating", "complete", "stopped", "error"]
    error: str | None
    scope: Scope | None
    created_at: str
    parts: list[AnswerPart]
    search_steps: list[SearchStep]
    citations: list[Citation]


ChatMessage = Annotated[UserMessage | AssistantMessage, Field(discriminator="role")]


class ChatSummary(BaseModel):
    id: str
    title: str | None
    updated_at: str
    generating: bool


class ChatList(BaseModel):
    library_ready: bool
    chats: list[ChatSummary]


class ChatDetail(BaseModel):
    id: str
    title: str | None
    scope: Scope | None
    generating: bool
    messages: list[ChatMessage]


class RenameRequest(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class AskRequest(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    scope: str | None = None
    """Doc id, or None for All documents."""
    upload_ids: list[str] = []


class AskResponse(BaseModel):
    question: UserMessage
    answer: AssistantMessage


class RetryResponse(BaseModel):
    answer: AssistantMessage


# Startup and shutdown


@asynccontextmanager
async def lifespan(
    app: FastAPI,
    config: Config,
    *,
    client_factory: Callable[[], Any] | None = None,
    runner_factory: Callable[..., Any] | None = None,
    embedder_factory: Callable[..., Any] | None = None,
) -> AsyncIterator[None]:
    """Start the query services (chat store, library cache, uploads and the single-doc worker,
    generation tasks) as app.state.query for `router`, and stop them in reverse order on exit.
    Entered inside the app's own lifespan.

    The keyword arguments replace the Anthropic client, single-doc runner and query embedder
    factories (tests)."""
    async with _services(config, client_factory, runner_factory, embedder_factory) as services:
        app.state.query = services
        try:
            yield
        finally:
            del app.state.query


@asynccontextmanager
async def _services(
    config: Config,
    client_factory: Callable[[], Any] | None,
    runner_factory: Callable[..., Any] | None,
    embedder_factory: Callable[..., Any] | None,
) -> AsyncIterator[QueryServices]:
    qconfig = load_query_config(config)
    async with AsyncExitStack() as stack:
        store = ChatStore(chat_db_path(config))
        stack.callback(store.close)
        stopped = store.mark_interrupted()
        if stopped:
            log.info("marked %d interrupted answers as stopped", stopped)

        library = LibraryCache(config, qconfig, embedder_factory=embedder_factory)
        stack.callback(library.close)
        try:
            await asyncio.to_thread(library.load)
        except LookupError as e:
            log.warning("library isn't ready: %s", e)
        threading.Thread(target=_warm, args=(library,), name="query-warm", daemon=True).start()

        upload_kwargs = {"runner_factory": runner_factory} if runner_factory is not None else {}
        uploads = Uploads(config, qconfig, store, library, **upload_kwargs)
        await uploads.start()
        stack.push_async_callback(uploads.close)

        generations = Generations(
            store, library, qconfig, log_dir=chat_logs_dir(config), client_factory=client_factory
        )
        stack.push_async_callback(generations.close)

        yield QueryServices(config, qconfig, store, library, uploads, generations)


def _warm(library: LibraryCache) -> None:
    try:
        library.warm()
    except Exception:
        log.exception("loading the query embedding model failed")


# Routes

router = APIRouter(prefix="/chat", tags=["chat"])


def get_services(request: Request) -> QueryServices:
    return request.app.state.query


Services = Annotated[QueryServices, Depends(get_services)]


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except ChatBusy:
        raise HTTPException(409, "An answer is already generating") from None
    except LibraryNotReady:
        raise HTTPException(409, "Library isn't ready") from None
    except UploadError as e:
        raise HTTPException(409, str(e)) from None
    except KeyError:
        raise HTTPException(404, "Not found") from None
    except LookupError as e:
        raise HTTPException(409, str(e) or "Nothing to retry") from None
    except ValueError as e:
        raise HTTPException(409, str(e)) from None


@router.get("/chats")
def list_chats(services: Services) -> ChatList:
    return ChatList(
        library_ready=services.library.ready,
        chats=[_summary(services, chat) for chat in services.store.list_chats()],
    )


@router.post("/chats")
def create_chat(services: Services) -> ChatSummary:
    return _summary(services, services.store.create_chat())


@router.get("/chats/{chat_id}")
def get_chat(chat_id: str, services: Services) -> ChatDetail:
    with _errors():
        chat = services.store.get_chat(chat_id)
    return ChatDetail(
        id=chat.id,
        title=chat.title,
        scope=_scope(services, chat.scope),
        generating=services.generations.generating(chat.id),
        messages=list(_messages(services, chat.id).values()),
    )


@router.patch("/chats/{chat_id}")
def rename_chat(chat_id: str, body: RenameRequest, services: Services) -> ChatSummary:
    with _errors():
        return _summary(services, services.store.update_chat(chat_id, title=body.title))


@router.delete("/chats/{chat_id}", status_code=204)
async def delete_chat(chat_id: str, services: Services) -> None:
    with _errors():
        services.store.get_chat(chat_id)
        await services.generations.cancel_chat(chat_id)
        services.store.delete_chat(chat_id)


@router.post("/chats/{chat_id}/messages", status_code=202)
async def ask(chat_id: str, body: AskRequest, services: Services) -> AskResponse:
    with _errors():
        question, answer = await services.generations.ask(
            chat_id, body.text, scope=body.scope, upload_ids=body.upload_ids
        )
    messages = _messages(services, chat_id)
    return AskResponse(question=messages[question.id], answer=messages[answer.id])


def _chat_events(chat_id: str, services: Services) -> AsyncIterator[ChatEvent]:
    with _errors():
        services.store.get_chat(chat_id)
    events = services.generations.subscribe(chat_id)
    if events is None:
        raise HTTPException(204)
    return events


@router.get(
    "/chats/{chat_id}/events",
    response_class=EventSourceResponse,
    responses={204: {"description": "No answer is generating"}},
)
async def chat_events(
    events: Annotated[AsyncIterator[ChatEvent], Depends(_chat_events)],
) -> AsyncIterator[ServerSentEvent]:
    async for event in events:
        yield ServerSentEvent(event=event.event, id=str(event.id), data=dict(event.data))
        if event.event in TERMINAL_EVENTS:
            break


@router.post("/chats/{chat_id}/retry", status_code=202)
async def retry(chat_id: str, services: Services) -> RetryResponse:
    with _errors():
        answer = await services.generations.retry(chat_id)
    return RetryResponse(answer=_messages(services, chat_id)[answer.id])


@router.post("/chats/{chat_id}/stop", status_code=204)
async def stop(chat_id: str, services: Services) -> None:
    with _errors():
        services.store.get_chat(chat_id)
        await services.generations.stop(chat_id)


@router.post("/uploads")
async def upload(file: UploadFile, services: Services) -> UploadCard:
    data = await file.read(MAX_BYTES + 1)
    with _errors():
        row = await services.uploads.add(file.filename or "upload.pdf", data)
    return _card(services, row)


def _upload_events(upload_id: str, services: Services) -> AsyncIterator[dict[str, Any]]:
    with _errors():
        services.uploads.get(upload_id)
    return services.uploads.events(upload_id)


@router.get("/uploads/{upload_id}/events", response_class=EventSourceResponse)
async def upload_events(
    events: Annotated[AsyncIterator[dict[str, Any]], Depends(_upload_events)],
) -> AsyncIterator[ServerSentEvent]:
    async for status in events:
        yield ServerSentEvent(event="status", data=status)


@router.delete("/uploads/{upload_id}")
async def remove_upload(upload_id: str, services: Services) -> UploadCard:
    with _errors():
        row = await services.uploads.remove(upload_id)
    return _card(services, row)


# Response builders


def _summary(services: QueryServices, chat: ChatRow) -> ChatSummary:
    return ChatSummary(
        id=chat.id,
        title=chat.title,
        updated_at=chat.updated_at,
        generating=services.generations.generating(chat.id),
    )


def _scope(services: QueryServices, doc_id: str | None) -> Scope | None:
    if doc_id is None:
        return None
    doc = services.library.document(doc_id)
    filename = doc.filename if doc is not None else None
    if filename is None:
        rows = services.store.uploads_for_doc(doc_id)
        filename = rows[-1].filename if rows else None
    return Scope(doc_id=doc_id, filename=filename)


def _card(services: QueryServices, row: UploadRow) -> UploadCard:
    doc = services.library.document(row.doc_id)
    return UploadCard(
        id=row.id,
        doc_id=row.doc_id,
        filename=row.filename,
        pages=row.pages,
        status=row.status,
        stage=row.stage,
        error=row.error,
        removable=row.removable,
        removed=row.removed,
        job_number=doc.job_number if doc is not None else None,
        project=doc.project if doc is not None else None,
    )


def _messages(services: QueryServices, chat_id: str) -> dict[str, UserMessage | AssistantMessage]:
    """The chat's messages by id, in seq order."""
    store = services.store
    rows = store.messages(chat_id)
    steps = store.search_steps(chat_id)
    citations = store.citations(chat_id)
    attachments = store.attachments(chat_id)
    removed: dict[str, bool] = {}
    scopes: dict[str | None, Scope | None] = {}

    def is_removed(doc_id: str) -> bool:
        if doc_id not in removed:
            removed[doc_id] = not services.library.contains(doc_id) and any(
                u.removed for u in store.uploads_for_doc(doc_id)
            )
        return removed[doc_id]

    def scope(doc_id: str | None) -> Scope | None:
        if doc_id not in scopes:
            scopes[doc_id] = _scope(services, doc_id)
        return scopes[doc_id]

    out: dict[str, UserMessage | AssistantMessage] = {}
    for row in rows:
        if row.role == "user":
            out[row.id] = UserMessage(
                id=row.id,
                role="user",
                text=row.text,
                scope=scope(row.scope),
                created_at=row.created_at,
                attachments=[_card(services, u) for u in attachments.get(row.id, [])],
            )
            continue
        cites = citations.get(row.id, [])
        out[row.id] = AssistantMessage(
            id=row.id,
            role="assistant",
            state=row.state,
            error=row.error,
            scope=scope(row.scope),
            created_at=row.created_at,
            parts=answer_parts(row, cites),
            search_steps=[
                SearchStep(
                    round=s.round,
                    tool=s.tool,
                    query=s.query,
                    scope=s.scope,
                    passages=s.passages,
                    error=s.error,
                )
                for s in steps.get(row.id, [])
            ],
            citations=[
                Citation(
                    n=c.n,
                    doc_id=c.doc_id,
                    chunk_id=c.chunk_id,
                    filename=c.filename,
                    pages=c.pages,
                    regions=c.regions,
                    cited_text=c.cited_text,
                    removed=is_removed(c.doc_id),
                )
                for c in cites
            ],
        )
    return out

