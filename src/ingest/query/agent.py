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

from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ingest.query import QueryConfig
from ingest.query.library import LibraryCache
from ingest.query.store import ChatStore, CitationRow, MessageRow

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic


class ChatBusy(Exception):
    """The chat already has an answer generating."""


class LibraryNotReady(Exception):
    """No current embed version: questions can't be answered."""


@dataclass(frozen=True)
class ChatEvent:
    id: int
    event: str
    data: Mapping[str, Any]


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
        raise NotImplementedError

    def generating(self, chat_id: str) -> bool:
        raise NotImplementedError

    async def ask(
        self, chat_id: str, text: str, *, scope: str | None, upload_ids: Sequence[str] = ()
    ) -> tuple[MessageRow, MessageRow]:
        """Store the question (with its attachments) and an assistant message in state
        'generating', set the chat's scope, and start the answer's task. Returns (question,
        answer). Raises KeyError for an unknown chat, ChatBusy, LibraryNotReady, or ValueError
        when `scope` isn't a library doc or an upload isn't ready."""
        raise NotImplementedError

    async def retry(self, chat_id: str) -> MessageRow:
        """Delete the latest answer and generate it again with its question's scope; returns the
        new answer row. Raises KeyError, ChatBusy, LibraryNotReady, or LookupError when the chat
        has no answer."""
        raise NotImplementedError

    async def stop(self, chat_id: str) -> None:
        """Stop the chat's answer, keeping its partial text (state 'stopped'). No-op when idle."""
        raise NotImplementedError

    def subscribe(self, chat_id: str) -> AsyncIterator[ChatEvent] | None:
        """The active answer's events from the first one, then live until done or error; None
        when the chat has no active answer. Disconnecting doesn't affect the answer."""
        raise NotImplementedError

    async def cancel_chat(self, chat_id: str) -> None:
        """Stop and wait for the chat's task (before deleting the chat)."""
        raise NotImplementedError

    async def close(self) -> None:
        """Stop every answer and store each as stopped (server shutdown)."""
        raise NotImplementedError


def answer_parts(message: MessageRow, citations: Sequence[CitationRow]) -> list[dict[str, Any]]:
    """An answer's visible content rebuilt from its stored API content, in the order a stream
    produces it: {"type": "text", "text"} and {"type": "citation", "n"} parts (the same sequence
    as its text_delta and citation events)."""
    raise NotImplementedError
