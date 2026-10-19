"""The agent (Query-Design §9–§10): the hand-written tool loop over the Claude API, tools over the
library, citation mapping, titles, and generation tasks (one asyncio task per chat, running
independently of requests, buffering their events for resubscribers)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi.sse import ServerSentEvent

from ingest.query import QuerySettings
from ingest.query.library import LibraryCache
from ingest.query.models import AskOut, MessageOut
from ingest.query.store import ChatStore


class Generations:
    def __init__(
        self, store: ChatStore, library: LibraryCache, settings: QuerySettings, *, client: Any | None = None
    ) -> None:
        """`client` is an anthropic.AsyncAnthropic (created lazily from ANTHROPIC_API_KEY or the
        `ant auth login` profile when None); tests pass a fake."""
        raise NotImplementedError

    def generating(self, chat_id: int) -> bool:
        raise NotImplementedError

    async def ask(self, chat_id: int, text: str, scope: str | None, upload_ids: list[int]) -> AskOut:
        """Store the question (with its attachments and scope; the chat's scope becomes `scope`),
        create the answer message in state 'generating' and start its task. Raises
        RuntimeError when an answer is already generating in the chat."""
        raise NotImplementedError

    async def retry(self, chat_id: int) -> MessageOut:
        """Drop the latest answer and regenerate it with its question's scope."""
        raise NotImplementedError

    async def stop(self, chat_id: int) -> None:
        """Stop the active answer, keeping its partial text (state 'stopped')."""
        raise NotImplementedError

    def subscribe(self, chat_id: int) -> AsyncIterator[ServerSentEvent]:
        """The active answer's events from the start, then live until done or error; a single
        `idle` event when nothing is generating (query.models documents the events)."""
        raise NotImplementedError

    def message_out(self, message_id: int) -> MessageOut:
        """A stored message as the API returns it (parts, search steps, citations, cards)."""
        raise NotImplementedError

    def recover(self) -> None:
        """On startup: mark answers left 'generating' by a restart as stopped."""
        raise NotImplementedError

    async def close(self) -> None:
        """Stop every active answer (stored as stopped)."""
        raise NotImplementedError
