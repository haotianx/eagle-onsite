"""chat.sqlite (Query-Design §14): chats, messages, search steps, citations, uploads and
attachments. Separate from the ingestion catalog; same conventions (stdlib sqlite3, WAL, one
connection guarded by a lock, safe to share across threads)."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeAlias

from ingest.fingerprint import canonical_json

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
BUSY_TIMEOUT_MS = 30_000

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


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _new_id() -> str:
    return uuid.uuid4().hex


def _loads(value: str | None) -> Any:
    return None if value is None else json.loads(value)


def _chat(r: sqlite3.Row) -> ChatRow:
    return ChatRow(
        id=r["id"], title=r["title"], scope=r["scope"], created_at=r["created_at"], updated_at=r["updated_at"]
    )


def _message(r: sqlite3.Row) -> MessageRow:
    return MessageRow(
        id=r["id"],
        chat_id=r["chat_id"],
        seq=r["seq"],
        role=r["role"],
        text=r["text"],
        content=json.loads(r["content"]),
        state=r["state"],
        error=r["error"],
        scope=r["scope"],
        model=r["model"],
        usage=_loads(r["usage"]),
        ttft_ms=r["ttft_ms"],
        total_ms=r["total_ms"],
        request_ids=json.loads(r["request_ids"]),
        created_at=r["created_at"],
    )


def _search_step(r: sqlite3.Row) -> SearchStepRow:
    return SearchStepRow(
        id=r["id"],
        message_id=r["message_id"],
        round=r["round"],
        tool=r["tool"],
        query=r["query"],
        scope=r["scope"],
        passages=json.loads(r["passages"]),
        error=r["error"],
    )


def _citation(r: sqlite3.Row) -> CitationRow:
    return CitationRow(
        message_id=r["message_id"],
        n=r["n"],
        source=r["source"],
        doc_id=r["doc_id"],
        chunk_id=r["chunk_id"],
        filename=r["filename"],
        pages=json.loads(r["pages"]),
        regions=json.loads(r["regions"]),
        cited_text=r["cited_text"],
    )


def _upload(r: sqlite3.Row) -> UploadRow:
    return UploadRow(
        id=r["id"],
        doc_id=r["doc_id"],
        filename=r["filename"],
        path=r["path"],
        pages=r["pages"],
        status=r["status"],
        stage=r["stage"],
        error=r["error"],
        run_id=r["run_id"],
        removable=bool(r["removable"]),
        removed=bool(r["removed"]),
        created_at=r["created_at"],
    )


class ChatStore:
    def __init__(self, path: Path) -> None:
        """Open (creating parent dirs and the schema if needed); WAL, foreign keys on."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._depth = 0
        conn = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA_PATH.read_text())
        self._conn = conn

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group several writes atomically (nestable, like Catalog.transaction)."""
        with self._lock:
            depth = self._depth
            savepoint = f"sp{depth}"
            self._conn.execute("BEGIN IMMEDIATE" if depth == 0 else f"SAVEPOINT {savepoint}")
            self._depth += 1
            try:
                yield
            except BaseException:
                self._depth -= 1
                if depth == 0:
                    self._conn.execute("ROLLBACK")
                else:
                    self._conn.execute(f"ROLLBACK TO {savepoint}")
                    self._conn.execute(f"RELEASE {savepoint}")
                raise
            self._depth -= 1
            self._conn.execute("COMMIT" if depth == 0 else f"RELEASE {savepoint}")

    def _all(self, sql: str, args: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def _get(self, what: str, sql: str, key: Any) -> sqlite3.Row:
        with self._lock:
            row = self._conn.execute(sql, (key,)).fetchone()
        if row is None:
            raise KeyError(f"{what} {key!r} not found")
        return row

    def _update(self, table: str, what: str, key: str, values: Mapping[str, Any]) -> None:
        """Set the columns in `values` that are not UNSET. Raises KeyError when the row is missing."""
        values = {k: v for k, v in values.items() if v is not UNSET}
        with self.transaction():
            if self._conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (key,)).fetchone() is None:
                raise KeyError(f"{what} {key!r} not found")
            if values:
                sql = f"UPDATE {table} SET {', '.join(f'{k} = ?' for k in values)} WHERE id = ?"
                self._conn.execute(sql, (*values.values(), key))

    def _by_message(self, sql: str, chat_id: str, convert: Any) -> dict[str, list[Any]]:
        out: dict[str, list[Any]] = {}
        for r in self._all(sql, (chat_id,)):
            out.setdefault(r["message_id"], []).append(convert(r))
        return out

    # Chats
    def create_chat(self) -> ChatRow:
        chat_id, now = _new_id(), _now()
        with self.transaction():
            self._conn.execute(
                "INSERT INTO chats (id, title, scope, created_at, updated_at) VALUES (?, NULL, NULL, ?, ?)",
                (chat_id, now, now),
            )
        return self.get_chat(chat_id)

    def get_chat(self, chat_id: str) -> ChatRow:
        """Raises KeyError when missing."""
        return _chat(self._get("chat", "SELECT * FROM chats WHERE id = ?", chat_id))

    def list_chats(self) -> list[ChatRow]:
        """Most recently updated first."""
        return [_chat(r) for r in self._all("SELECT * FROM chats ORDER BY updated_at DESC, created_at DESC")]

    def update_chat(self, chat_id: str, *, title: str | None = UNSET, scope: str | None = UNSET) -> ChatRow:
        """Set the given columns and updated_at. Raises KeyError when missing."""
        with self.transaction():
            self._update("chats", "chat", chat_id, {"title": title, "scope": scope, "updated_at": _now()})
            return self.get_chat(chat_id)

    def delete_chat(self, chat_id: str) -> None:
        """Delete the chat with its messages, search steps, citations and attachments. Uploads
        are kept. Raises KeyError when missing."""
        with self.transaction():
            if self._conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,)).rowcount == 0:
                raise KeyError(f"chat {chat_id!r} not found")

    def clear_scope(self, doc_id: str) -> list[str]:
        """Set scope to None on chats scoped to doc_id (a removed upload); returns their ids.
        Leaves updated_at unchanged."""
        with self.transaction():
            cur = self._conn.execute("UPDATE chats SET scope = NULL WHERE scope = ? RETURNING id", (doc_id,))
            rows = cur.fetchall()
        return [r["id"] for r in rows]

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
        message_id, now = _new_id(), _now()
        content_json = canonical_json([dict(m) for m in content])
        with self.transaction():
            if self._conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now, chat_id)).rowcount == 0:
                raise KeyError(f"chat {chat_id!r} not found")
            seq = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE chat_id = ?", (chat_id,)
            ).fetchone()[0]
            self._conn.execute(
                "INSERT INTO messages (id, chat_id, seq, role, text, content, state, scope, model, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (message_id, chat_id, seq, role, text, content_json, state, scope, model, now),
            )
            for position, upload_id in enumerate(upload_ids):
                self.get_upload(upload_id)
                self._conn.execute(
                    "INSERT INTO attachments (message_id, upload_id, position) VALUES (?, ?, ?)",
                    (message_id, upload_id, position),
                )
            return self.get_message(message_id)

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
        values = {
            "text": text,
            "content": content if content is UNSET else canonical_json([dict(m) for m in content]),
            "state": state,
            "error": error,
            "model": model,
            "usage": usage if usage is UNSET or usage is None else canonical_json(dict(usage)),
            "ttft_ms": ttft_ms,
            "total_ms": total_ms,
            "request_ids": request_ids if request_ids is UNSET else canonical_json(list(request_ids)),
        }
        with self.transaction():
            self._update("messages", "message", message_id, values)
            return self.get_message(message_id)

    def get_message(self, message_id: str) -> MessageRow:
        """Raises KeyError when missing."""
        return _message(self._get("message", "SELECT * FROM messages WHERE id = ?", message_id))

    def messages(self, chat_id: str) -> list[MessageRow]:
        """In seq order."""
        return [_message(r) for r in self._all("SELECT * FROM messages WHERE chat_id = ? ORDER BY seq", (chat_id,))]

    def delete_message(self, message_id: str) -> None:
        """Delete one message with its search steps, citations and attachments (Retry drops the
        latest answer)."""
        with self.transaction():
            if self._conn.execute("DELETE FROM messages WHERE id = ?", (message_id,)).rowcount == 0:
                raise KeyError(f"message {message_id!r} not found")

    def mark_interrupted(self) -> int:
        """At startup: messages still 'generating' (the server stopped mid-answer) become
        'stopped'. Returns how many changed."""
        with self.transaction():
            return self._conn.execute("UPDATE messages SET state = 'stopped' WHERE state = 'generating'").rowcount

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
        with self.transaction():
            self.get_message(message_id)
            step_id = self._conn.execute(
                "INSERT INTO search_steps (message_id, round, tool, query, scope, passages, error)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (message_id, round, tool, query, scope, canonical_json([dict(p) for p in passages]), error),
            ).lastrowid
            return _search_step(self._get("search step", "SELECT * FROM search_steps WHERE id = ?", step_id))

    def search_steps(self, chat_id: str) -> dict[str, list[SearchStepRow]]:
        """Steps of every message in the chat: message id -> steps in insertion order."""
        return self._by_message(
            "SELECT s.* FROM search_steps s JOIN messages m ON m.id = s.message_id"
            " WHERE m.chat_id = ? ORDER BY m.seq, s.id",
            chat_id,
            _search_step,
        )

    def add_citations(self, message_id: str, citations: Sequence[CitationRow]) -> None:
        """Insert (replacing any with the same message_id and n)."""
        with self.transaction():
            self.get_message(message_id)
            self._conn.executemany(
                "INSERT OR REPLACE INTO citations"
                " (message_id, n, source, doc_id, chunk_id, filename, pages, regions, cited_text)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        message_id,
                        c.n,
                        c.source,
                        c.doc_id,
                        c.chunk_id,
                        c.filename,
                        canonical_json(list(c.pages)),
                        canonical_json([dict(r) for r in c.regions]),
                        c.cited_text,
                    )
                    for c in citations
                ],
            )

    def citations(self, chat_id: str) -> dict[str, list[CitationRow]]:
        """Citations of every message in the chat: message id -> citations ordered by n."""
        return self._by_message(
            "SELECT c.* FROM citations c JOIN messages m ON m.id = c.message_id"
            " WHERE m.chat_id = ? ORDER BY m.seq, c.n",
            chat_id,
            _citation,
        )

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
        upload_id = _new_id()
        with self.transaction():
            self._conn.execute(
                "INSERT INTO uploads (id, doc_id, filename, path, pages, status, stage, error, run_id, removable,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (upload_id, doc_id, filename, path, pages, status, stage, error, run_id, int(removable), _now()),
            )
            return self.get_upload(upload_id)

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
        values = {"status": status, "stage": stage, "error": error, "run_id": run_id, "pages": pages}
        with self.transaction():
            self._update("uploads", "upload", upload_id, values)
            return self.get_upload(upload_id)

    def get_upload(self, upload_id: str) -> UploadRow:
        """Raises KeyError when missing."""
        return _upload(self._get("upload", "SELECT * FROM uploads WHERE id = ?", upload_id))

    def uploads_for_doc(self, doc_id: str) -> list[UploadRow]:
        """Oldest first."""
        rows = self._all("SELECT * FROM uploads WHERE doc_id = ? ORDER BY created_at, rowid", (doc_id,))
        return [_upload(r) for r in rows]

    def processing_uploads(self) -> list[UploadRow]:
        """Rows with status 'processing' and not removed, oldest first."""
        rows = self._all(
            "SELECT * FROM uploads WHERE status = 'processing' AND removed = 0 ORDER BY created_at, rowid"
        )
        return [_upload(r) for r in rows]

    def set_doc_removed(self, doc_id: str, removed: bool) -> None:
        """Set `removed` on every upload row of the doc."""
        with self.transaction():
            self._conn.execute("UPDATE uploads SET removed = ? WHERE doc_id = ?", (int(removed), doc_id))

    def attachments(self, chat_id: str) -> dict[str, list[UploadRow]]:
        """Uploads attached to each message of the chat: message id -> rows in attach order."""
        return self._by_message(
            "SELECT a.message_id, u.* FROM attachments a JOIN messages m ON m.id = a.message_id"
            " JOIN uploads u ON u.id = a.upload_id WHERE m.chat_id = ? ORDER BY m.seq, a.position",
            chat_id,
            _upload,
        )
