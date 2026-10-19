"""/chat routes and SSE (Query-Design §13), mounted by ingest.api.app under /api."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, status
from fastapi.sse import EventSourceResponse, ServerSentEvent

from ingest.config import Config
from ingest.query import QuerySettings
from ingest.query.agent import Generations
from ingest.query.library import LibraryCache
from ingest.query.models import AskIn, AskOut, ChatDetail, ChatPatch, ChatSummary, LibraryStatus, MessageOut, UploadOut
from ingest.query.store import ChatStore
from ingest.query.uploads import Uploads
from ingest.store.catalog import Catalog
from ingest.store.objects import ObjectStore

CHAT_DB = "chat.sqlite"

router = APIRouter(prefix="/chat", tags=["chat"])


@dataclass
class ChatServices:
    settings: QuerySettings
    store: ChatStore
    library: LibraryCache
    generations: Generations
    uploads: Uploads

    def start(self) -> None:
        self.generations.recover()
        self.uploads.start()

    async def close(self) -> None:
        await self.generations.close()
        self.uploads.close()
        self.store.close()


def create_services(
    config: Config,
    catalog: Catalog,
    objects: ObjectStore,
    *,
    client: Any | None = None,
    runner_factory: Any | None = None,
    embedder: Any | None = None,
) -> ChatServices:
    """Build the chat services over the app's catalog and object store. `client`,
    `runner_factory` and `embedder` replace the Anthropic client, the single-doc runner and the
    query embedder (tests)."""
    settings = QuerySettings.from_config(config)
    store = ChatStore(config.data_dir / CHAT_DB)
    library = LibraryCache(catalog, objects, settings, embedder=embedder)
    return ChatServices(
        settings=settings,
        store=store,
        library=library,
        generations=Generations(store, library, settings, client=client),
        uploads=Uploads(config, catalog, store, library, settings, runner_factory=runner_factory),
    )


def get_services(request: Request) -> ChatServices:
    return request.app.state.chat


def _summary(s: ChatServices, chat_id: int) -> ChatSummary:
    chat = s.store.get_chat(chat_id)
    return ChatSummary(id=chat.id, title=chat.title, updated=chat.updated, generating=s.generations.generating(chat.id))


def _chat_or_404(s: ChatServices, chat_id: int) -> None:
    try:
        s.store.get_chat(chat_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Chat not found") from None


@router.get("/library")
def library_status(s: ChatServices = Depends(get_services)) -> LibraryStatus:
    ready = s.library.ready()
    return LibraryStatus(ready=ready, documents=s.library.document_count() if ready else 0)


@router.get("/chats")
def list_chats(s: ChatServices = Depends(get_services)) -> list[ChatSummary]:
    return [_summary(s, c.id) for c in s.store.list_chats()]


@router.post("/chats")
def create_chat(s: ChatServices = Depends(get_services)) -> ChatSummary:
    return _summary(s, s.store.create_chat().id)


@router.get("/chats/{chat_id}")
def get_chat(chat_id: int, s: ChatServices = Depends(get_services)) -> ChatDetail:
    _chat_or_404(s, chat_id)
    chat = s.store.get_chat(chat_id)
    return ChatDetail(
        **_summary(s, chat_id).model_dump(),
        scope=chat.scope,
        messages=[s.generations.message_out(m.id) for m in s.store.messages(chat_id)],
    )


@router.patch("/chats/{chat_id}")
def update_chat(chat_id: int, body: ChatPatch, s: ChatServices = Depends(get_services)) -> ChatSummary:
    _chat_or_404(s, chat_id)
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    s.store.update_chat(chat_id, **changes)
    return _summary(s, chat_id)


@router.delete("/chats/{chat_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_chat(chat_id: int, s: ChatServices = Depends(get_services)) -> None:
    _chat_or_404(s, chat_id)
    await s.generations.stop(chat_id)
    s.store.delete_chat(chat_id)


@router.post("/chats/{chat_id}/messages")
async def ask(chat_id: int, body: AskIn, s: ChatServices = Depends(get_services)) -> AskOut:
    _chat_or_404(s, chat_id)
    if not s.library.ready():
        raise HTTPException(status.HTTP_409_CONFLICT, "Library isn't ready")
    try:
        return await s.generations.ask(chat_id, body.text, body.scope, body.upload_ids)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None


@router.get("/chats/{chat_id}/events", response_class=EventSourceResponse)
async def chat_events(chat_id: int, s: ChatServices = Depends(get_services)) -> AsyncIterator[ServerSentEvent]:
    _chat_or_404(s, chat_id)
    async for event in s.generations.subscribe(chat_id):
        yield event


@router.post("/chats/{chat_id}/retry")
async def retry(chat_id: int, s: ChatServices = Depends(get_services)) -> MessageOut:
    _chat_or_404(s, chat_id)
    try:
        return await s.generations.retry(chat_id)
    except RuntimeError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from None


@router.post("/chats/{chat_id}/stop", status_code=status.HTTP_204_NO_CONTENT)
async def stop(chat_id: int, s: ChatServices = Depends(get_services)) -> None:
    _chat_or_404(s, chat_id)
    await s.generations.stop(chat_id)


@router.post("/uploads")
async def create_upload(file: UploadFile, s: ChatServices = Depends(get_services)) -> UploadOut:
    return await s.uploads.create(file.filename or "upload.pdf", await file.read())


@router.get("/uploads/{upload_id}/events", response_class=EventSourceResponse)
async def upload_events(upload_id: int, s: ChatServices = Depends(get_services)) -> AsyncIterator[ServerSentEvent]:
    try:
        s.uploads.get(upload_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found") from None
    async for event in s.uploads.events(upload_id):
        yield event


@router.delete("/uploads/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_upload(upload_id: int, s: ChatServices = Depends(get_services)) -> None:
    try:
        await s.uploads.remove(upload_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Upload not found") from None
    except PermissionError as e:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(e)) from None
