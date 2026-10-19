"""chat.sqlite (Query-Design §14): chats, messages, search steps, citations, uploads and
attachments. Separate from the ingestion catalog; same conventions (stdlib sqlite3, WAL, one
connection guarded by a lock, safe to share across threads)."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeAlias

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

Role: TypeAlias = Literal["user", "assistant"]
MessageState: TypeAlias = Literal["generating", "complete", "stopped", "error"]
UploadStatus: TypeAlias = Literal["processing", "ready", "failed"]


class _Unset:
    def __repr__(self) -> str:
        return "UNSET"


UNSET: Any = _Unset()
"""Default for update_* keyword arguments: leave the column unchanged."""


@dataclass(frozen=True)
class ChatRow:
    id: str
    title: str | None
    scope: str | None
    """Doc id, or None for All documents."""
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class MessageRow:
    id: str
    chat_id: str
    seq: int
    role: Role
    text: str
    content: list[dict[str, Any]]
    """API messages ({role, content}) this message adds to the history, replayed unchanged."""
    state: MessageState
    error: str | None
    scope: str | None
    model: str | None
    usage: dict[str, Any] | None
    ttft_ms: float | None
    total_ms: float | None
    request_ids: list[str]
    created_at: str


@dataclass(frozen=True)
class SearchStepRow:
    id: int
    message_id: str
    round: int
    tool: str
    query: str | None
    scope: str | None
    passages: list[dict[str, Any]]
    error: str | None


@dataclass(frozen=True)
class CitationRow:
    message_id: str
    n: int
    source: str
    doc_id: str
    chunk_id: str | None
    filename: str | None
    pages: list[int]
    regions: list[dict[str, Any]]
    cited_text: str


@dataclass(frozen=True)
class UploadRow:
    id: str
    doc_id: str
    filename: str
    path: str | None
    pages: int | None
    status: UploadStatus
    stage: str | None
    error: str | None
    run_id: int | None
    removable: bool
    removed: bool
    created_at: str


class ChatStore:
    def __init__(self, path: Path) -> None:
        """Open (creating parent dirs and the schema if needed); WAL, foreign keys on."""
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group several writes atomically (nestable, like Catalog.transaction)."""
        raise NotImplementedError

    # Chats
    def create_chat(self) -> ChatRow:
        raise NotImplementedError

    def get_chat(self, chat_id: str) -> ChatRow:
        """Raises KeyError when missing."""
        raise NotImplementedError

    def list_chats(self) -> list[ChatRow]:
        """Most recently updated first."""
        raise NotImplementedError

    def update_chat(self, chat_id: str, *, title: str | None = UNSET, scope: str | None = UNSET) -> ChatRow:
        """Set the given columns and updated_at. Raises KeyError when missing."""
        raise NotImplementedError

    def delete_chat(self, chat_id: str) -> None:
        """Delete the chat with its messages, search steps, citations and attachments. Uploads
        are kept. Raises KeyError when missing."""
        raise NotImplementedError

    def clear_scope(self, doc_id: str) -> list[str]:
        """Set scope to None on chats scoped to doc_id (a removed upload); returns their ids.
        Leaves updated_at unchanged."""
        raise NotImplementedError

    # Messages
    def add_message(
        self,
        chat_id: str,
        *,
        role: Role,
        text: str,
        content: Sequence[Mapping[str, Any]],
        state: MessageState,
        scope: str | None,
        model: str | None = None,
        upload_ids: Sequence[str] = (),
    ) -> MessageRow:
        """Append with the next seq, attach `upload_ids` in order, and touch the chat's
        updated_at."""
        raise NotImplementedError

    def update_message(
        self,
        message_id: str,
        *,
        text: str = UNSET,
        content: Sequence[Mapping[str, Any]] = UNSET,
        state: MessageState = UNSET,
        error: str | None = UNSET,
        model: str | None = UNSET,
        usage: Mapping[str, Any] | None = UNSET,
        ttft_ms: float | None = UNSET,
        total_ms: float | None = UNSET,
        request_ids: Sequence[str] = UNSET,
    ) -> MessageRow:
        """Set the given columns. Raises KeyError when missing."""
        raise NotImplementedError

    def get_message(self, message_id: str) -> MessageRow:
        """Raises KeyError when missing."""
        raise NotImplementedError

    def messages(self, chat_id: str) -> list[MessageRow]:
        """In seq order."""
        raise NotImplementedError

    def delete_message(self, message_id: str) -> None:
        """Delete one message with its search steps, citations and attachments (Retry drops the
        latest answer)."""
        raise NotImplementedError

    def mark_interrupted(self) -> int:
        """At startup: messages still 'generating' (the server stopped mid-answer) become
        'stopped'. Returns how many changed."""
        raise NotImplementedError

    # Search steps and citations
    def add_search_step(
        self,
        message_id: str,
        *,
        round: int,
        tool: str,
        query: str | None,
        scope: str | None,
        passages: Sequence[Mapping[str, Any]],
        error: str | None = None,
    ) -> SearchStepRow:
        raise NotImplementedError

    def search_steps(self, chat_id: str) -> dict[str, list[SearchStepRow]]:
        """Steps of every message in the chat: message id -> steps in insertion order."""
        raise NotImplementedError

    def add_citations(self, message_id: str, citations: Sequence[CitationRow]) -> None:
        """Insert (replacing any with the same message_id and n)."""
        raise NotImplementedError

    def citations(self, chat_id: str) -> dict[str, list[CitationRow]]:
        """Citations of every message in the chat: message id -> citations ordered by n."""
        raise NotImplementedError

    # Uploads and attachments
    def add_upload(
        self,
        *,
        doc_id: str,
        filename: str,
        status: UploadStatus,
        removable: bool,
        path: str | None = None,
        pages: int | None = None,
        stage: str | None = None,
        error: str | None = None,
        run_id: int | None = None,
    ) -> UploadRow:
        raise NotImplementedError

    def update_upload(
        self,
        upload_id: str,
        *,
        status: UploadStatus = UNSET,
        stage: str | None = UNSET,
        error: str | None = UNSET,
        run_id: int | None = UNSET,
        pages: int | None = UNSET,
    ) -> UploadRow:
        """Set the given columns. Raises KeyError when missing."""
        raise NotImplementedError

    def get_upload(self, upload_id: str) -> UploadRow:
        """Raises KeyError when missing."""
        raise NotImplementedError

    def uploads_for_doc(self, doc_id: str) -> list[UploadRow]:
        """Oldest first."""
        raise NotImplementedError

    def processing_uploads(self) -> list[UploadRow]:
        """Rows with status 'processing' and not removed, oldest first."""
        raise NotImplementedError

    def set_doc_removed(self, doc_id: str, removed: bool) -> None:
        """Set `removed` on every upload row of the doc."""
        raise NotImplementedError

    def attachments(self, chat_id: str) -> dict[str, list[UploadRow]]:
        """Uploads attached to each message of the chat: message id -> rows in attach order."""
        raise NotImplementedError
