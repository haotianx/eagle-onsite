"""The agent (Query-Design §9–§10): Claude API loop with the library tools, citation mapping,
history replay, titles, and generation tasks that run independently of requests.

Events of one answer (ChatEvent.event, data):
    search_started   {round, tool, query, scope}          scope: doc id or None
    search_finished  {round, tool, query, scope, passages, error}
                     passages: Passage.to_dict() / DocumentInfo.to_dict() list; error: str or None
    text_delta       {text}
    citation         {n, source, doc_id, chunk_id, filename, pages, regions, cited_text}
                     emitted where the marker goes: after the text of the cited block so far
    title            {title}                              the chat's generated title
    done             {message_id, state, usage}           state: complete or stopped
    error            {message_id, kind}                   kind: connection, rate_limit, api, refusal
Event ids count from 1 within an answer; a subscriber always gets the answer's events from the
first one."""

from __future__ import annotations

import asyncio
import json
import time
import traceback
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anthropic

from ingest.query import QueryConfig, prompts
from ingest.query.library import LIST_LIMIT, DocumentInfo, LibraryCache, Passage
from ingest.query.store import ChatStore, CitationRow, MessageRow

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic

MAX_TOKENS = 64000
TITLE_MAX_TOKENS = 1000
TITLE_MAX_CHARS = 60
FALLBACK_BETA = "server-side-fallback-2026-07-01"
LOG_FILE = "chat.jsonl"
ALL_DOCUMENTS = "All documents"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_documents",
        "description": (
            "Search the documents in scope for passages relevant to a query. Returns the best "
            "matching passages with their document and pages."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "What to look for, in natural language."}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "read_document",
        "description": (
            "Read every passage of the document in scope, in order. Only applies when one document is in scope."
        ),
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "list_documents",
        "description": (
            f"List documents in the library matching every given filter, up to {LIST_LIMIT}. "
            "Only applies when All documents is in scope."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "job_number": {"type": "string", "description": "Exact job number."},
                "project": {"type": "string", "description": "Part of the project name."},
                "filename_contains": {"type": "string", "description": "Part of the filename."},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
]

_LIST_FILTERS = ("job_number", "project", "filename_contains")
_READ_OUT_OF_SCOPE = "read_document only applies when one document is in scope. Use search_documents or list_documents."
_LIST_OUT_OF_SCOPE = (
    "list_documents only applies when All documents is in scope. Use search_documents or read_document."
)
_UNFINISHED_BLOCKS = {"thinking", "redacted_thinking", "tool_use"}


class ChatBusy(Exception):
    """The chat already has an answer generating."""


class LibraryNotReady(Exception):
    """No current embed version: questions can't be answered."""


class _Refused(Exception):
    pass


@dataclass(frozen=True)
class ChatEvent:
    id: int
    event: str
    data: Mapping[str, Any]


@dataclass(eq=False)
class _Generation:
    chat_id: str
    message_id: str
    question: str
    scope: str | None
    started: float
    task: asyncio.Task[None] | None = None
    events: list[ChatEvent] = field(default_factory=list)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    running: bool = False
    """The task has started (a task cancelled before its first step would skip _run)."""
    finished: bool = False
    added: list[dict[str, Any]] = field(default_factory=list)
    """API messages this answer added so far (completed calls and tool results)."""
    pending: Any = None
    """Message snapshot of the call in flight."""
    snapshots: list[Any] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Cited source -> Passage or DocumentInfo dict from this chat's search steps."""
    citations: dict[str, CitationRow] = field(default_factory=dict)
    block_sources: dict[int, set[str]] = field(default_factory=dict)
    request_ids: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    rounds: int = 0
    stop_reason: str | None = None
    ttft_ms: float | None = None

    def emit(self, event: str, data: Mapping[str, Any]) -> None:
        self.events.append(ChatEvent(len(self.events) + 1, event, data))
        self.changed.set()
        self.changed = asyncio.Event()

    @property
    def request_id(self) -> str | None:
        return self.request_ids[-1] if self.request_ids else None


class _ChatLog:
    """JSON lines appended to <log_dir>/chat.jsonl (Query-Design §17)."""

    def __init__(self, log_dir: Path) -> None:
        self.path = log_dir / LOG_FILE

    def write(self, event: str, gen: _Generation, *, level: str = "INFO", **fields: Any) -> None:
        line = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": level,
            "event": event,
            "chat_id": gen.chat_id,
            "message_id": gen.message_id,
            "request_id": gen.request_id,
            **fields,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")


class Generations:
    """One asyncio task per chat with an answer generating; each buffers its answer's events for
    subscribers. Owned by the API server; all methods run on its event loop."""

    def __init__(
        self,
        store: ChatStore,
        library: LibraryCache,
        qconfig: QueryConfig,
        *,
        log_dir: Path,
        client_factory: Callable[[], AsyncAnthropic] | None = None,
    ) -> None:
        """No I/O. `client_factory` defaults to anthropic.AsyncAnthropic (credential from
        ANTHROPIC_API_KEY or an `ant auth login` profile); the client is created on first use."""
        self.store = store
        self.library = library
        self.qconfig = qconfig
        self._log = _ChatLog(log_dir)
        self._client_factory = client_factory or anthropic.AsyncAnthropic
        self._client: AsyncAnthropic | None = None
        self._active: dict[str, _Generation] = {}

    def generating(self, chat_id: str) -> bool:
        return chat_id in self._active

    async def ask(
        self, chat_id: str, text: str, *, scope: str | None, upload_ids: Sequence[str] = ()
    ) -> tuple[MessageRow, MessageRow]:
        """Store the question (with its attachments) and an assistant message in state
        'generating', set the chat's scope, and start the answer's task. Returns (question,
        answer). Raises KeyError for an unknown chat, ChatBusy, LibraryNotReady, or ValueError
        when `scope` isn't a library doc or an upload isn't ready."""
        await self._check_ready(chat_id)
        if scope is not None and not self.library.contains(scope):
            raise ValueError(f"document {scope!r} is not in the library")
        for upload_id in upload_ids:
            try:
                upload = self.store.get_upload(upload_id)
            except KeyError:
                raise ValueError(f"upload {upload_id!r} not found") from None
            if upload.status != "ready" or upload.removed:
                raise ValueError(f"upload {upload_id!r} is not ready")
        with self.store.transaction():
            question = self.store.add_message(
                chat_id,
                role="user",
                text=text,
                content=question_content(text, self._scope_name(scope)),
                state="complete",
                scope=scope,
                upload_ids=upload_ids,
            )
            answer = self._add_answer(chat_id, scope)
            self.store.update_chat(chat_id, scope=scope)
        self._start(answer, text)
        return question, answer

    async def retry(self, chat_id: str) -> MessageRow:
        """Delete the latest answer and generate it again with its question's scope; returns the
        new answer row. Raises KeyError, ChatBusy, LibraryNotReady, or LookupError when the chat
        has no answer."""
        await self._check_ready(chat_id)
        messages = self.store.messages(chat_id)
        if len(messages) < 2 or messages[-1].role != "assistant" or messages[-2].role != "user":
            raise LookupError(f"chat {chat_id!r} has no answer to retry")
        question = messages[-2]
        with self.store.transaction():
            self.store.delete_message(messages[-1].id)
            answer = self._add_answer(chat_id, question.scope)
        self._start(answer, question.text)
        return answer

    async def stop(self, chat_id: str) -> None:
        """Stop the chat's answer, keeping its partial text (state 'stopped'). No-op when idle."""
        gen = self._active.get(chat_id)
        if gen is not None:
            await _cancel([gen])

    def subscribe(self, chat_id: str) -> AsyncIterator[ChatEvent] | None:
        """The active answer's events from the first one, then live until done or error; None
        when the chat has no active answer. Disconnecting doesn't affect the answer."""
        gen = self._active.get(chat_id)
        return None if gen is None else _follow(gen)

    async def cancel_chat(self, chat_id: str) -> None:
        """Stop and wait for the chat's task (before deleting the chat)."""
        await self.stop(chat_id)

    async def close(self) -> None:
        """Stop every answer and store each as stopped (server shutdown)."""
        await _cancel(list(self._active.values()))
        if self._client is not None:
            await self._client.close()
            self._client = None

    # Setup

    async def _check_ready(self, chat_id: str) -> None:
        self.store.get_chat(chat_id)
        if self.generating(chat_id):
            raise ChatBusy(chat_id)
        try:
            await asyncio.to_thread(self.library.reload_if_stale)
        except LookupError as e:
            raise LibraryNotReady(str(e)) from e
        if not self.library.ready:
            raise LibraryNotReady("no library loaded")
        if self.generating(chat_id):
            raise ChatBusy(chat_id)

    def _add_answer(self, chat_id: str, scope: str | None) -> MessageRow:
        return self.store.add_message(
            chat_id, role="assistant", text="", content=[], state="generating", scope=scope, model=self.qconfig.model
        )

    def _scope_name(self, scope: str | None) -> str | None:
        if scope is None:
            return None
        doc = self.library.document(scope)
        return (doc.filename if doc is not None else None) or scope

    def _start(self, answer: MessageRow, question: str) -> None:
        gen = _Generation(answer.chat_id, answer.id, question, answer.scope, time.perf_counter())
        self._active[gen.chat_id] = gen
        gen.task = asyncio.create_task(self._run(gen), name=f"chat-{gen.chat_id}")

    def _client_(self) -> AsyncAnthropic:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    # The answer's task

    async def _run(self, gen: _Generation) -> None:
        gen.running = True
        state, error = "complete", None
        try:
            try:
                await self._loop(gen)
            except asyncio.CancelledError:
                state = "stopped"
            except _Refused:
                state, error = "error", "refusal"
            except anthropic.APIConnectionError:
                state, error = "error", "connection"
            except anthropic.RateLimitError as e:
                state, error = "error", "rate_limit"
                self._keep_request_id(gen, e)
            except anthropic.APIError as e:
                state, error = "error", "api"
                self._keep_request_id(gen, e)
            except Exception:
                state, error = "error", "api"
                self._log.write("exception", gen, level="ERROR", traceback=traceback.format_exc())
            usage = self._finish(gen, state, error)
            if state == "complete":
                try:
                    await self._title(gen)
                except asyncio.CancelledError:
                    pass
                except Exception:
                    self._log.write("title_failed", gen, level="WARNING", traceback=traceback.format_exc())
            if error is None:
                gen.emit("done", {"message_id": gen.message_id, "state": state, "usage": usage})
            else:
                gen.emit("error", {"message_id": gen.message_id, "kind": error})
        finally:
            gen.finished = True
            gen.changed.set()
            if self._active.get(gen.chat_id) is gen:
                del self._active[gen.chat_id]

    async def _loop(self, gen: _Generation) -> None:
        gen.sources = self._chat_sources(gen.chat_id)
        history = self._history(gen.chat_id, exclude=gen.message_id)
        while True:
            final = gen.rounds >= self.qconfig.max_rounds
            snapshot = await self._call(gen, history + gen.added, final=final)
            gen.pending = None
            gen.stop_reason = snapshot.stop_reason
            if snapshot.stop_reason == "refusal":
                # A declined response's partial output is incomplete: keep it out of the history.
                raise _Refused
            blocks = _clean_blocks(snapshot.content)
            gen.added.append({"role": "assistant", "content": blocks})
            tool_uses = [b for b in blocks if b["type"] == "tool_use"]
            if snapshot.stop_reason != "tool_use" or not tool_uses or final:
                return
            gen.rounds += 1
            results = [await self._run_tool(gen, gen.rounds, block) for block in tool_uses]
            gen.added.append({"role": "user", "content": results})

    def _params(self, messages: list[dict[str, Any]], *, final: bool) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.qconfig.model,
            "max_tokens": MAX_TOKENS,
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.qconfig.effort},
            "cache_control": {"type": "ephemeral"},
            "system": prompts.SYSTEM,
            "tools": TOOLS,
            "messages": messages,
        }
        if final:
            params["tool_choice"] = {"type": "none"}
        return params

    async def _call(self, gen: _Generation, messages: list[dict[str, Any]], *, final: bool) -> Any:
        gen.block_sources = {}
        async with self._client_().beta.messages.stream(**self._params(messages, final=final)) as stream:
            if stream.request_id:
                gen.request_ids.append(stream.request_id)
            async for event in stream:
                if event.type == "message_start":
                    gen.pending = stream.current_message_snapshot
                    gen.snapshots.append(gen.pending)
                elif event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        if gen.ttft_ms is None:
                            gen.ttft_ms = (time.perf_counter() - gen.started) * 1000
                        gen.text.append(event.delta.text)
                        gen.emit("text_delta", {"text": event.delta.text})
                    elif event.delta.type == "citations_delta":
                        self._cite(gen, event.index, event.delta.citation)
            snapshot = stream.current_message_snapshot
        self._log.write(
            "call",
            gen,
            round=gen.rounds,
            final=final,
            model=snapshot.model,
            stop_reason=snapshot.stop_reason,
            usage=_usage([snapshot]),
        )
        return snapshot

    def _finish(self, gen: _Generation, state: str, error: str | None) -> dict[str, int]:
        content = list(gen.added)
        if gen.pending is not None:
            content.append({"role": "assistant", "content": _clean_blocks(gen.pending.content)})
        usage = _usage(gen.snapshots)
        model = str(gen.snapshots[-1].model) if gen.snapshots else self.qconfig.model
        total_ms = (time.perf_counter() - gen.started) * 1000
        self.store.update_message(
            gen.message_id,
            text="".join(gen.text),
            content=content,
            state=state,
            error=error,
            model=model,
            usage=usage,
            ttft_ms=gen.ttft_ms,
            total_ms=total_ms,
            request_ids=gen.request_ids,
        )
        self._log.write(
            "answer",
            gen,
            state=state,
            error=error,
            queries=gen.queries,
            rounds=gen.rounds,
            model=model,
            usage=usage,
            ttft_ms=gen.ttft_ms,
            total_ms=total_ms,
            stop_reason=gen.stop_reason,
            request_ids=gen.request_ids,
            citations=len(gen.citations),
        )
        return usage

    @staticmethod
    def _keep_request_id(gen: _Generation, e: anthropic.APIError) -> None:
        request_id = getattr(e, "request_id", None)
        if request_id and request_id not in gen.request_ids:
            gen.request_ids.append(request_id)

    # History

    def _history(self, chat_id: str, *, exclude: str) -> list[dict[str, Any]]:
        """The chat's stored API messages for the next request (see replay_content)."""
        out: list[dict[str, Any]] = []
        question: list[dict[str, Any]] = []
        for row in self.store.messages(chat_id):
            if row.id == exclude:
                continue
            if row.role == "user":
                out += _without_scope(question)
                question = list(row.content)
                continue
            answer = replay_content(row.content)
            if not any(m["role"] == "assistant" for m in answer):
                question = _without_scope(question)
            out += question + answer
            question = []
        return out + question

    # Tools

    async def _run_tool(self, gen: _Generation, round: int, block: Mapping[str, Any]) -> dict[str, Any]:
        tool, args, scope = block["name"], block.get("input") or {}, gen.scope
        query: str | None = None
        items: list[Passage] | list[DocumentInfo] = []
        error: str | None = None
        if tool == "search_documents":
            query = str(args.get("query", ""))
        elif tool == "list_documents":
            filters = {k: str(args[k]) for k in _LIST_FILTERS if args.get(k)}
            query = ", ".join(f"{k}: {v}" for k, v in filters.items()) or "all"
        if query is not None:
            gen.queries.append(query)
        gen.emit("search_started", {"round": round, "tool": tool, "query": query, "scope": scope})
        try:
            if tool == "search_documents":
                items = await asyncio.to_thread(self.library.search, query, doc_id=scope, k=self.qconfig.top_k)
            elif tool == "read_document":
                if scope is None:
                    error = _READ_OUT_OF_SCOPE
                else:
                    items = await asyncio.to_thread(self.library.read, scope)
            elif tool == "list_documents":
                if scope is not None:
                    error = _LIST_OUT_OF_SCOPE
                else:
                    items = await asyncio.to_thread(self.library.list_documents, **filters)
            else:
                error = f"Unknown tool {tool}."
        except LookupError:
            error = "The library isn't available right now."
        dicts = [item.to_dict() for item in items]
        for d in dicts:
            gen.sources[d["chunk_id"] if tool != "list_documents" else d["doc_id"]] = d
        self.store.add_search_step(
            gen.message_id, round=round, tool=tool, query=query, scope=scope, passages=dicts, error=error
        )
        gen.emit(
            "search_finished",
            {"round": round, "tool": tool, "query": query, "scope": scope, "passages": dicts, "error": error},
        )
        self._log.write(
            "tool",
            gen,
            round=round,
            tool=tool,
            query=query,
            scope=scope,
            error=error,
            results=[[d.get("chunk_id") or d["doc_id"], d.get("score")] for d in dicts],
        )
        if error is not None:
            content: list[dict[str, Any]] = [{"type": "text", "text": error}]
            return {"type": "tool_result", "tool_use_id": block["id"], "content": content, "is_error": True}
        if not items:
            empty = "No documents match." if tool == "list_documents" else "No passages found."
            content = [{"type": "text", "text": empty}]
        elif tool == "list_documents":
            content = [_document_result(d) for d in items]  # type: ignore[arg-type]
        else:
            content = [_passage_result(p) for p in items]  # type: ignore[arg-type]
        return {"type": "tool_result", "tool_use_id": block["id"], "content": content}

    # Citations

    def _chat_sources(self, chat_id: str) -> dict[str, dict[str, Any]]:
        sources: dict[str, dict[str, Any]] = {}
        for steps in self.store.search_steps(chat_id).values():
            for step in steps:
                key = "doc_id" if step.tool == "list_documents" else "chunk_id"
                for d in step.passages:
                    sources[d[key]] = d
        return sources

    def _resolve(self, gen: _Generation, source: str) -> dict[str, Any] | None:
        found = gen.sources.get(source)
        if found is None:
            item = self.library.passage(source) or self.library.document(source)
            found = item.to_dict() if item is not None else None
        return found

    def _cite(self, gen: _Generation, index: int, citation: Any) -> None:
        if citation.type != "search_result_location":
            return
        source = citation.source
        seen = gen.block_sources.setdefault(index, set())
        if source in seen:
            return
        row = gen.citations.get(source)
        if row is None:
            found = self._resolve(gen, source)
            if found is None:
                self._log.write("unresolved_citation", gen, level="WARNING", source=source)
                return
            passage = "chunk_id" in found
            row = CitationRow(
                message_id=gen.message_id,
                n=len(gen.citations) + 1,
                source=source,
                doc_id=found["doc_id"],
                chunk_id=found["chunk_id"] if passage else None,
                filename=found.get("filename"),
                pages=list(found["pages"]) if passage else [1],
                regions=list(found["regions"]) if passage else [],
                cited_text=citation.cited_text,
            )
            gen.citations[source] = row
            self.store.add_citations(gen.message_id, [row])
        seen.add(source)
        gen.emit(
            "citation",
            {
                "n": row.n,
                "source": row.source,
                "doc_id": row.doc_id,
                "chunk_id": row.chunk_id,
                "filename": row.filename,
                "pages": row.pages,
                "regions": row.regions,
                "cited_text": row.cited_text,
            },
        )

    # Titles

    async def _title(self, gen: _Generation) -> None:
        if self.store.get_chat(gen.chat_id).title is not None:
            return
        response = await self._client_().beta.messages.create(
            model=self.qconfig.model,
            max_tokens=TITLE_MAX_TOKENS,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": prompts.title_prompt(gen.question)}],
        )
        raw = "".join(b.text for b in response.content if b.type == "text")
        title = clean_title(raw)
        self._log.write(
            "title",
            gen,
            title_request_id=getattr(response, "_request_id", None),
            usage=_usage([response]),
            title=title,
        )
        if title:
            self.store.update_chat(gen.chat_id, title=title)
            gen.emit("title", {"title": title})


async def _cancel(gens: Sequence[_Generation]) -> None:
    while not all(gen.running for gen in gens):
        await asyncio.sleep(0)
    tasks = [gen.task for gen in gens if gen.task is not None]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait(tasks)


async def _follow(gen: _Generation) -> AsyncIterator[ChatEvent]:
    i = 0
    while True:
        while i < len(gen.events):
            yield gen.events[i]
            i += 1
        if gen.finished:
            return
        await gen.changed.wait()


def question_content(text: str, scope_name: str | None) -> list[dict[str, Any]]:
    """API messages a question adds to the history: the question, then its scope note as a
    mid-conversation system message (`scope_name` None for All documents)."""
    return [
        {"role": "user", "content": [{"type": "text", "text": text}]},
        {"role": "system", "content": prompts.scope_message(scope_name)},
    ]


def _without_scope(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A question's messages without its scope note, for an answer that added no assistant turn
    (a system message must be last or followed by an assistant message)."""
    return [m for m in messages if m["role"] != "system"]


def replay_content(content: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """An answer's stored API messages as replayed: only completed blocks. Drops thinking without
    a signature, empty text blocks, and an unfinished tool round (an assistant turn whose
    tool_use blocks have no tool_result turn after it) with anything after it. A no-op for a
    completed answer."""
    out: list[dict[str, Any]] = []
    for i, message in enumerate(content):
        if message["role"] != "assistant":
            out.append(dict(message))
            continue
        blocks = message["content"]
        has_tools = any(b.get("type") == "tool_use" for b in blocks)
        answered = i + 1 < len(content) and content[i + 1]["role"] == "user"
        if has_tools and not answered:
            break
        kept = [b for b in blocks if _completed(b)]
        if kept:
            out.append({"role": "assistant", "content": kept})
    return out


def _completed(block: Mapping[str, Any]) -> bool:
    if block.get("type") == "thinking":
        return bool(block.get("signature"))
    if block.get("type") == "text":
        return bool(block.get("text"))
    return True


def _clean_blocks(blocks: Sequence[Any]) -> list[dict[str, Any]]:
    """Response content blocks as plain dicts the API accepts back. After a server-side fallback,
    thinking and tool_use blocks before the last fallback block and the fallback blocks
    themselves are dropped; text is kept."""
    dicts = [b.to_dict(mode="json", exclude_none=True) for b in blocks]
    last = max((i for i, b in enumerate(dicts) if b["type"] == "fallback"), default=-1)
    return [
        b for i, b in enumerate(dicts) if b["type"] != "fallback" and (i > last or b["type"] not in _UNFINISHED_BLOCKS)
    ]


def _usage(messages: Sequence[Any]) -> dict[str, int]:
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    return {k: sum(getattr(m.usage, k, None) or 0 for m in messages) for k in keys}


def compact_pages(pages: Sequence[int]) -> str:
    """[3] -> "3", [3, 4] -> "3–4", [1, 3] -> "1, 3", [1, 2, 3, 5] -> "1–3, 5"."""
    runs: list[list[int]] = []
    for p in sorted(set(pages)):
        if runs and p == runs[-1][1] + 1:
            runs[-1][1] = p
        else:
            runs.append([p, p])
    return ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in runs)


def _passage_result(p: Passage) -> dict[str, Any]:
    title = p.filename or p.doc_id
    if p.pages:
        title += f" · p. {compact_pages(p.pages)}"
    if p.also_in:
        title += f" · also in {p.also_in} other document{'s' if p.also_in != 1 else ''}"
    return {
        "type": "search_result",
        "source": p.chunk_id,
        "title": title,
        "content": [{"type": "text", "text": p.text or "(empty)"}],
        "citations": {"enabled": True},
    }


def _document_result(d: DocumentInfo) -> dict[str, Any]:
    parts = [
        f"Job # {d.job_number}" if d.job_number else None,
        f"Project: {d.project}" if d.project else None,
        f"Folder: {d.subfolder}" if d.subfolder else None,
        f"{d.pages} page{'s' if d.pages != 1 else ''}" if d.pages is not None else None,
        d.paths[0] if d.paths else None,
    ]
    return {
        "type": "search_result",
        "source": d.doc_id,
        "title": d.filename or d.doc_id,
        "content": [{"type": "text", "text": " · ".join(p for p in parts if p) or d.doc_id}],
        "citations": {"enabled": True},
    }


def clean_title(raw: str) -> str:
    lines = [line.strip() for line in raw.strip().splitlines() if line.strip()]
    title = lines[0] if lines else ""
    title = title.strip().strip("\"'“”‘’`*#").strip().rstrip(".").strip()
    if len(title) > TITLE_MAX_CHARS:
        title = title[:TITLE_MAX_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:-–")
    return title


def answer_parts(message: MessageRow, citations: Sequence[CitationRow]) -> list[dict[str, Any]]:
    """An answer's visible content rebuilt from its stored API content, in the order a stream
    produces it: {"type": "text", "text"} and {"type": "citation", "n"} parts (the same sequence
    as its text_delta and citation events)."""
    numbers = {c.source: c.n for c in citations}
    parts: list[dict[str, Any]] = []
    for api_message in message.content:
        if api_message["role"] != "assistant":
            continue
        for block in api_message["content"]:
            if block.get("type") != "text":
                continue
            if block.get("text"):
                if parts and parts[-1]["type"] == "text":
                    parts[-1]["text"] += block["text"]
                else:
                    parts.append({"type": "text", "text": block["text"]})
            seen: set[str] = set()
            for citation in block.get("citations") or ():
                source = citation.get("source")
                if source in numbers and source not in seen:
                    seen.add(source)
                    parts.append({"type": "citation", "n": numbers[source]})
    return parts
