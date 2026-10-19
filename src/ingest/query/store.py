"""Chat store: `.ingest/chat.sqlite` (Query-Design §14), separate from the ingestion catalog.

WAL mode with one connection shared across threads behind a lock, like the catalog. JSON columns
are decoded on read. Timestamps are UTC ISO-8601 strings. get_* methods raise KeyError when the
row is missing.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeAlias

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
BUSY_TIMEOUT_MS = 30_000

Role: TypeAlias = Literal["user", "assistant"]
MessageState: TypeAlias = Literal["generating", "complete", "stopped", "error"]
UploadStatus: TypeAlias = Literal["processing", "ready", "failed"]


class _Unset:
    pass


UNSET: Any = _Unset()


@dataclass(frozen=True)
class ChatRow:
    id: int
    title: str | None
    scope: str | None
    """Doc id, or None for All documents."""
    created: str
    updated: str


@dataclass(frozen=True)
class MessageRow:
    id: int
    chat_id: int
    seq: int
    role: Role
    content: list[dict[str, Any]]
    """The API messages ({role, content}) this message adds to the history, replayed unchanged."""
    state: MessageState
    error: str | None
    scope: str | None
    model: str | None
    usage: dict[str, Any] | None
    latency: dict[str, Any] | None
    request_id: str | None
    created: str


@dataclass(frozen=True)
class SearchStepRow:
    id: int
    message_id: int
    round: int
    tool: str
    query: str | None
    scope: str | None
    passages: list[dict[str, Any]]


@dataclass(frozen=True)
class CitationRow:
    message_id: int
    n: int
    doc_id: str
    chunk_id: str | None
    filename: str | None
    pages: tuple[int, ...]
    regions: tuple[dict[str, Any], ...]
    cited_text: str | None


@dataclass(frozen=True)
class UploadRow:
    id: int
    doc_id: str | None
    filename: str
    status: UploadStatus
    error: str | None
    removed: bool
    removable: bool
    run_id: int | None
    created: str


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _loads(text: str | None) -> Any:
    return None if text is None else json.loads(text)


def _chat(r: sqlite3.Row) -> ChatRow:
    return ChatRow(id=r["id"], title=r["title"], scope=r["scope"], created=r["created"], updated=r["updated"])


def _message(r: sqlite3.Row) -> MessageRow:
    return MessageRow(
        id=r["id"],
        chat_id=r["chat"],
        seq=r["seq"],
        role=r["role"],
        content=json.loads(r["content"]),
        state=r["state"],
        error=r["error"],
        scope=r["scope"],
        model=r["model"],
        usage=_loads(r["usage"]),
        latency=_loads(r["latency"]),
        request_id=r["request_id"],
        created=r["created"],
    )


def _step(r: sqlite3.Row) -> SearchStepRow:
    return SearchStepRow(
        id=r["id"],
        message_id=r["message"],
        round=r["round"],
        tool=r["tool"],
        query=r["query"],
        scope=r["scope"],
        passages=json.loads(r["passages"]),
    )


def _citation(r: sqlite3.Row) -> CitationRow:
    return CitationRow(
        message_id=r["message"],
        n=r["n"],
        doc_id=r["doc_id"],
        chunk_id=r["chunk_id"],
        filename=r["filename"],
        pages=tuple(json.loads(r["pages"])),
        regions=tuple(json.loads(r["regions"])),
        cited_text=r["cited_text"],
    )


def _upload(r: sqlite3.Row) -> UploadRow:
    return UploadRow(
        id=r["id"],
        doc_id=r["doc_id"],
        filename=r["filename"],
        status=r["status"],
        error=r["error"],
        removed=bool(r["removed"]),
        removable=bool(r["removable"]),
        run_id=r["run_id"],
        created=r["created"],
    )


class ChatStore:
    def __init__(self, path: Path) -> None:
        """Open (creating parent dirs and schema if needed)."""
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
        """Group several writes atomically. Methods called inside don't commit on their own."""
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

    def _update(self, table: str, row_id: int, values: Mapping[str, Any]) -> None:
        values = {k: v for k, v in values.items() if v is not UNSET}
        if not values:
            return
        sets = ", ".join(f"{k} = ?" for k in values)
        with self.transaction():
            cur = self._conn.execute(f"UPDATE {table} SET {sets} WHERE id = ?", (*values.values(), row_id))
            if cur.rowcount == 0:
                raise KeyError(f"{table[:-1]} {row_id!r} not found")

    # Chats
    def create_chat(self) -> ChatRow:
        now = _now()
        with self.transaction():
            chat_id = self._conn.execute(
                "INSERT INTO chats (title, scope, created, updated) VALUES (NULL, NULL, ?, ?)", (now, now)
            ).lastrowid
        return self.get_chat(chat_id)

    def get_chat(self, chat_id: int) -> ChatRow:
        return _chat(self._get("chat", "SELECT * FROM chats WHERE id = ?", chat_id))

    def list_chats(self) -> list[ChatRow]:
        """Most recently updated first."""
        return [_chat(r) for r in self._all("SELECT * FROM chats ORDER BY updated DESC, id DESC")]

    def update_chat(self, chat_id: int, *, title: str | None = UNSET, scope: str | None = UNSET) -> ChatRow:
        """Rename or change the scope; neither changes `updated`."""
        self._update("chats", chat_id, {"title": title, "scope": scope})
        return self.get_chat(chat_id)

    def delete_chat(self, chat_id: int) -> None:
        """Deletes its messages, search steps, citations and attachments; uploads stay."""
        with self.transaction():
            cur = self._conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
            if cur.rowcount == 0:
                raise KeyError(f"chat {chat_id!r} not found")

    def clear_scope(self, doc_id: str) -> list[int]:
        """Return chats scoped to `doc_id` to All documents; returns their ids."""
        with self.transaction():
            ids = [r["id"] for r in self._conn.execute("SELECT id FROM chats WHERE scope = ?", (doc_id,))]
            self._conn.execute("UPDATE chats SET scope = NULL WHERE scope = ?", (doc_id,))
        return ids

    # Messages
    def add_message(
        self,
        chat_id: int,
        role: Role,
        content: Sequence[Mapping[str, Any]],
        *,
        state: MessageState,
        scope: str | None,
        model: str | None = None,
    ) -> MessageRow:
        """Appends with the next seq and touches the chat's `updated`."""
        now = _now()
        with self.transaction():
            seq = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE chat = ?", (chat_id,)
            ).fetchone()[0]
            message_id = self._conn.execute(
                "INSERT INTO messages (chat, seq, role, content, state, scope, model, created)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (chat_id, seq, role, _dumps(list(content)), state, scope, model, now),
            ).lastrowid
            self._conn.execute("UPDATE chats SET updated = ? WHERE id = ?", (now, chat_id))
        return self.get_message(message_id)

    def update_message(
        self,
        message_id: int,
        *,
        content: Sequence[Mapping[str, Any]] = UNSET,
        state: MessageState = UNSET,
        error: str | None = UNSET,
        usage: Mapping[str, Any] | None = UNSET,
        latency: Mapping[str, Any] | None = UNSET,
        request_id: str | None = UNSET,
    ) -> MessageRow:
        def enc(v: Any) -> Any:
            return v if v is UNSET or v is None else _dumps(v if not isinstance(v, Mapping) else dict(v))

        self._update(
            "messages",
            message_id,
            {
                "content": UNSET if content is UNSET else _dumps(list(content)),
                "state": state,
                "error": error,
                "usage": enc(usage),
                "latency": enc(latency),
                "request_id": request_id,
            },
        )
        return self.get_message(message_id)

    def get_message(self, message_id: int) -> MessageRow:
        return _message(self._get("message", "SELECT * FROM messages WHERE id = ?", message_id))

    def messages(self, chat_id: int) -> list[MessageRow]:
        """In seq order."""
        return [_message(r) for r in self._all("SELECT * FROM messages WHERE chat = ? ORDER BY seq", (chat_id,))]

    def delete_message(self, message_id: int) -> None:
        """Also deletes its search steps, citations and attachments."""
        with self.transaction():
            cur = self._conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            if cur.rowcount == 0:
                raise KeyError(f"message {message_id!r} not found")

    def messages_in_state(self, state: MessageState) -> list[MessageRow]:
        """E.g. 'generating' messages left by a server restart."""
        return [_message(r) for r in self._all("SELECT * FROM messages WHERE state = ? ORDER BY id", (state,))]

    # Search steps
    def add_search_step(
        self,
        message_id: int,
        *,
        round: int,
        tool: str,
        query: str | None,
        scope: str | None,
        passages: Sequence[Mapping[str, Any]],
    ) -> SearchStepRow:
        with self.transaction():
            step_id = self._conn.execute(
                "INSERT INTO search_steps (message, round, tool, query, scope, passages) VALUES (?, ?, ?, ?, ?, ?)",
                (message_id, round, tool, query, scope, _dumps([dict(p) for p in passages])),
            ).lastrowid
        return _step(self._get("search step", "SELECT * FROM search_steps WHERE id = ?", step_id))

    def search_steps(self, message_id: int) -> list[SearchStepRow]:
        return [_step(r) for r in self._all("SELECT * FROM search_steps WHERE message = ? ORDER BY id", (message_id,))]

    # Citations
    def add_citation(
        self,
        message_id: int,
        *,
        n: int,
        doc_id: str,
        chunk_id: str | None,
        filename: str | None,
        pages: Sequence[int],
        regions: Sequence[Mapping[str, Any]],
        cited_text: str | None,
    ) -> CitationRow:
        """Insert or replace by (message, n)."""
        with self.transaction():
            self._conn.execute(
                "INSERT OR REPLACE INTO citations (message, n, doc_id, chunk_id, filename, pages, regions, cited_text)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message_id,
                    n,
                    doc_id,
                    chunk_id,
                    filename,
                    _dumps(list(pages)),
                    _dumps([dict(r) for r in regions]),
                    cited_text,
                ),
            )
        return next(c for c in self.citations(message_id) if c.n == n)

    def citations(self, message_id: int) -> list[CitationRow]:
        return [_citation(r) for r in self._all("SELECT * FROM citations WHERE message = ? ORDER BY n", (message_id,))]

    # Uploads
    def add_upload(
        self,
        filename: str,
        *,
        doc_id: str | None,
        status: UploadStatus,
        removable: bool,
        error: str | None = None,
        run_id: int | None = None,
    ) -> UploadRow:
        with self.transaction():
            upload_id = self._conn.execute(
                "INSERT INTO uploads (doc_id, filename, status, error, removable, run_id, created)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (doc_id, filename, status, error, int(removable), run_id, _now()),
            ).lastrowid
        return self.get_upload(upload_id)

    def get_upload(self, upload_id: int) -> UploadRow:
        return _upload(self._get("upload", "SELECT * FROM uploads WHERE id = ?", upload_id))

    def update_upload(
        self,
        upload_id: int,
        *,
        status: UploadStatus = UNSET,
        error: str | None = UNSET,
        run_id: int | None = UNSET,
        removed: bool = UNSET,
    ) -> UploadRow:
        self._update(
            "uploads",
            upload_id,
            {"status": status, "error": error, "run_id": run_id, "removed": UNSET if removed is UNSET else int(removed)},
        )
        return self.get_upload(upload_id)

    def uploads(self, *, status: UploadStatus | None = None, include_removed: bool = False) -> list[UploadRow]:
        sql, args = "SELECT * FROM uploads WHERE 1 = 1", []
        if status is not None:
            sql += " AND status = ?"
            args.append(status)
        if not include_removed:
            sql += " AND removed = 0"
        return [_upload(r) for r in self._all(sql + " ORDER BY id", args)]

    def uploads_for_doc(self, doc_id: str) -> list[UploadRow]:
        return [_upload(r) for r in self._all("SELECT * FROM uploads WHERE doc_id = ? ORDER BY id", (doc_id,))]

    def mark_doc_removed(self, doc_id: str) -> None:
        """Mark every upload of the doc removed."""
        with self.transaction():
            self._conn.execute("UPDATE uploads SET removed = 1 WHERE doc_id = ?", (doc_id,))

    # Attachments
    def attach(self, message_id: int, upload_ids: Sequence[int]) -> None:
        with self.transaction():
            self._conn.executemany(
                "INSERT OR IGNORE INTO attachments (message, upload) VALUES (?, ?)",
                [(message_id, u) for u in upload_ids],
            )

    def attachments(self, message_id: int) -> list[UploadRow]:
        rows = self._all(
            "SELECT u.* FROM attachments a JOIN uploads u ON u.id = a.upload WHERE a.message = ? ORDER BY u.id",
            (message_id,),
        )
        return [_upload(r) for r in rows]

    def chat_uploads(self, chat_id: int) -> list[UploadRow]:
        """Uploads attached to any message of the chat, in first-attached order."""
        rows = self._all(
            "SELECT u.* FROM attachments a JOIN messages m ON m.id = a.message JOIN uploads u ON u.id = a.upload"
            " WHERE m.chat = ? GROUP BY u.id ORDER BY MIN(m.seq), u.id",
            (chat_id,),
        )
        return [_upload(r) for r in rows]
