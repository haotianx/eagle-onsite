"""SQLite catalog: snapshots, file index, cache index, runs, events, versions, current, issues,
metrics. Schema lives in schema.sql and is created on open.

Concurrency: WAL mode. Many readers (API, CLI); writes come from one process per run (the
executor parent) plus short CLI/API actions (promote, cancel). Use a busy timeout. JSON columns are
stored with fingerprint.canonical_json and decoded on read. Timestamps are UTC ISO-8601 strings.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeAlias

from ingest.contract import Issue

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
BUSY_TIMEOUT_MS = 30_000
_IN_CHUNK = 500


RunStatus: TypeAlias = Literal["queued", "running", "succeeded", "failed", "cancelled"]
VersionStatus: TypeAlias = Literal["building", "complete", "abandoned"]
DocStatus: TypeAlias = Literal["ok", "error", "skipped"]
IssueSource: TypeAlias = Literal["hard", "quality", "script"]


@dataclass(frozen=True)
class SnapshotDocInput:
    doc_id: str
    paths: tuple[str, ...]
    size: int
    mtime_ns: int
    meta: Mapping[str, Any] = field(default_factory=dict)
    flags: tuple[str, ...] = ()
    flag_reason: str | None = None


@dataclass(frozen=True)
class SnapshotRow:
    id: int
    source: str
    created_at: str
    doc_count: int
    file_count: int


@dataclass(frozen=True)
class SnapshotDocRow:
    snapshot_id: int
    doc_id: str
    paths: tuple[str, ...]
    size: int
    mtime_ns: int
    meta: Mapping[str, Any]
    flags: tuple[str, ...]
    flag_reason: str | None


@dataclass(frozen=True)
class CacheEntry:
    node_fp: str
    doc_id: str
    object_sha: str
    kind: str
    duration_s: float | None
    created_at: str


@dataclass(frozen=True)
class RunRow:
    id: int
    dag: str
    request: Mapping[str, Any]
    status: RunStatus
    scratch: bool
    commit: str | None
    dirty: bool
    created_at: str
    started_at: str | None
    ended_at: str | None
    error: str | None
    cancel_requested: bool


@dataclass(frozen=True)
class EventRow:
    id: int
    run_id: int
    ts: str
    kind: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class VersionRow:
    id: int
    dag: str
    node: str
    seq: int | None
    """Assigned on finalize (v1, v2, … per dag/node); None while building or if abandoned."""
    node_fp: str
    snapshot_id: int
    input_versions: Mapping[str, int]
    run_id: int | None
    commit: str | None
    dirty: bool
    scratch: bool
    artifact_type: str | None
    meta: Mapping[str, Any]
    """Script hash, params, env hash, describe() output, …"""
    stats: Mapping[str, Any]
    status: VersionStatus
    created_at: str
    finalized_at: str | None

    @property
    def label(self) -> str:
        return f"{self.dag}/{self.node}@v{self.seq}" if self.seq is not None else f"{self.dag}/{self.node}@building"


@dataclass(frozen=True)
class VersionDocInput:
    doc_id: str
    status: DocStatus
    object_sha: str | None = None
    kind: str | None = None
    cached: bool = False
    error_type: str | None = None
    error_message: str | None = None
    traceback: str | None = None
    duration_s: float | None = None
    peak_rss_mb: float | None = None
    worker_pid: int | None = None


@dataclass(frozen=True)
class VersionDocRow(VersionDocInput):
    version_id: int = 0


@dataclass(frozen=True)
class IssueRow:
    id: int
    version_id: int
    doc_id: str | None
    source: IssueSource
    checks_fp: str | None
    level: str
    code: str
    message: str
    page: int | None


@dataclass(frozen=True)
class MetricRow:
    run_id: int
    node: str | None
    doc_id: str | None
    name: str
    value: float


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _chunks(items: Sequence[str]) -> Iterator[Sequence[str]]:
    for i in range(0, len(items), _IN_CHUNK):
        yield items[i : i + _IN_CHUNK]


def _snapshot(r: sqlite3.Row) -> SnapshotRow:
    return SnapshotRow(
        id=r["id"], source=r["source"], created_at=r["created_at"], doc_count=r["doc_count"], file_count=r["file_count"]
    )


def _snapshot_doc(r: sqlite3.Row) -> SnapshotDocRow:
    return SnapshotDocRow(
        snapshot_id=r["snapshot_id"],
        doc_id=r["doc_id"],
        paths=tuple(json.loads(r["paths"])),
        size=r["size"],
        mtime_ns=r["mtime_ns"],
        meta=json.loads(r["meta"]),
        flags=tuple(json.loads(r["flags"])),
        flag_reason=r["flag_reason"],
    )


def _cache(r: sqlite3.Row) -> CacheEntry:
    return CacheEntry(
        node_fp=r["node_fp"],
        doc_id=r["doc_id"],
        object_sha=r["object_sha"],
        kind=r["kind"],
        duration_s=r["duration_s"],
        created_at=r["created_at"],
    )


def _run(r: sqlite3.Row) -> RunRow:
    return RunRow(
        id=r["id"],
        dag=r["dag"],
        request=json.loads(r["request"]),
        status=r["status"],
        scratch=bool(r["scratch"]),
        commit=r["git_commit"],
        dirty=bool(r["dirty"]),
        created_at=r["created_at"],
        started_at=r["started_at"],
        ended_at=r["ended_at"],
        error=r["error"],
        cancel_requested=bool(r["cancel_requested"]),
    )


def _event(r: sqlite3.Row) -> EventRow:
    return EventRow(id=r["id"], run_id=r["run_id"], ts=r["ts"], kind=r["kind"], payload=json.loads(r["payload"]))


def _version(r: sqlite3.Row) -> VersionRow:
    return VersionRow(
        id=r["id"],
        dag=r["dag"],
        node=r["node"],
        seq=r["seq"],
        node_fp=r["node_fp"],
        snapshot_id=r["snapshot_id"],
        input_versions=json.loads(r["input_versions"]),
        run_id=r["run_id"],
        commit=r["git_commit"],
        dirty=bool(r["dirty"]),
        scratch=bool(r["scratch"]),
        artifact_type=r["artifact_type"],
        meta=json.loads(r["meta"]),
        stats=json.loads(r["stats"]),
        status=r["status"],
        created_at=r["created_at"],
        finalized_at=r["finalized_at"],
    )


def _version_doc(r: sqlite3.Row) -> VersionDocRow:
    return VersionDocRow(
        version_id=r["version_id"],
        doc_id=r["doc_id"],
        status=r["status"],
        object_sha=r["object_sha"],
        kind=r["kind"],
        cached=bool(r["cached"]),
        error_type=r["error_type"],
        error_message=r["error_message"],
        traceback=r["traceback"],
        duration_s=r["duration_s"],
        peak_rss_mb=r["peak_rss_mb"],
        worker_pid=r["worker_pid"],
    )


def _issue(r: sqlite3.Row) -> IssueRow:
    return IssueRow(
        id=r["id"],
        version_id=r["version_id"],
        doc_id=r["doc_id"],
        source=r["source"],
        checks_fp=r["checks_fp"],
        level=r["level"],
        code=r["code"],
        message=r["message"],
        page=r["page"],
    )


class Catalog:
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

    def _one(self, sql: str, args: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, args).fetchone()

    def _get(self, what: str, sql: str, key: Any) -> sqlite3.Row:
        row = self._one(sql, (key,))
        if row is None:
            raise KeyError(f"{what} {key!r} not found")
        return row

    # Snapshots
    def create_snapshot(self, source: str, docs: Sequence[SnapshotDocInput], file_count: int) -> int:
        with self.transaction():
            now = _now()
            self._conn.execute("INSERT OR IGNORE INTO sources (name, created_at) VALUES (?, ?)", (source, now))
            snapshot_id = self._conn.execute(
                "INSERT INTO snapshots (source, created_at, doc_count, file_count) VALUES (?, ?, ?, ?)",
                (source, now, len(docs), file_count),
            ).lastrowid
            self._conn.executemany(
                "INSERT INTO snapshot_docs (snapshot_id, doc_id, primary_path, paths, size, mtime_ns, meta, flags,"
                " flag_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        snapshot_id,
                        d.doc_id,
                        d.paths[0] if d.paths else "",
                        _dumps(list(d.paths)),
                        d.size,
                        d.mtime_ns,
                        _dumps(dict(d.meta)),
                        _dumps(list(d.flags)),
                        d.flag_reason,
                    )
                    for d in docs
                ],
            )
        return snapshot_id

    def latest_snapshot(self, source: str) -> SnapshotRow | None:
        row = self._one("SELECT * FROM snapshots WHERE source = ? ORDER BY id DESC LIMIT 1", (source,))
        return _snapshot(row) if row else None

    def get_snapshot(self, snapshot_id: int) -> SnapshotRow:
        """Raises KeyError if missing (all get_* methods do)."""
        return _snapshot(self._get("snapshot", "SELECT * FROM snapshots WHERE id = ?", snapshot_id))

    def list_snapshots(self, source: str | None = None) -> list[SnapshotRow]:
        """Newest first."""
        if source is None:
            rows = self._all("SELECT * FROM snapshots ORDER BY id DESC")
        else:
            rows = self._all("SELECT * FROM snapshots WHERE source = ? ORDER BY id DESC", (source,))
        return [_snapshot(r) for r in rows]

    def snapshot_docs(self, snapshot_id: int) -> list[SnapshotDocRow]:
        """Ordered by primary path."""
        rows = self._all(
            "SELECT * FROM snapshot_docs WHERE snapshot_id = ? ORDER BY primary_path, doc_id", (snapshot_id,)
        )
        return [_snapshot_doc(r) for r in rows]

    def snapshot_doc(self, snapshot_id: int, doc_id: str) -> SnapshotDocRow | None:
        row = self._one("SELECT * FROM snapshot_docs WHERE snapshot_id = ? AND doc_id = ?", (snapshot_id, doc_id))
        return _snapshot_doc(row) if row else None

    # File index (lets sync skip re-hashing unchanged files)
    def file_index_get(self, source: str) -> dict[str, tuple[int, int, str]]:
        """rel_path → (size, mtime_ns, sha)."""
        rows = self._all("SELECT rel_path, size, mtime_ns, sha FROM file_index WHERE source = ?", (source,))
        return {r["rel_path"]: (r["size"], r["mtime_ns"], r["sha"]) for r in rows}

    def file_index_put(self, source: str, entries: Mapping[str, tuple[int, int, str]]) -> None:
        """Replace the source's index with `entries`."""
        with self.transaction():
            self._conn.execute("DELETE FROM file_index WHERE source = ?", (source,))
            self._conn.executemany(
                "INSERT INTO file_index (source, rel_path, size, mtime_ns, sha) VALUES (?, ?, ?, ?, ?)",
                [(source, p, size, mtime, sha) for p, (size, mtime, sha) in entries.items()],
            )

    # Cache index
    def cache_get(self, node_fp: str, doc_id: str) -> CacheEntry | None:
        row = self._one("SELECT * FROM cache WHERE node_fp = ? AND doc_id = ?", (node_fp, doc_id))
        return _cache(row) if row else None

    def cache_get_many(self, node_fp: str, doc_ids: Sequence[str]) -> dict[str, CacheEntry]:
        out: dict[str, CacheEntry] = {}
        ids = list(dict.fromkeys(doc_ids))
        for chunk in _chunks(ids):
            marks = ",".join("?" * len(chunk))
            rows = self._all(f"SELECT * FROM cache WHERE node_fp = ? AND doc_id IN ({marks})", (node_fp, *chunk))
            out.update((r["doc_id"], _cache(r)) for r in rows)
        return out

    def cache_put(self, node_fp: str, doc_id: str, object_sha: str, kind: str, duration_s: float | None) -> None:
        """Insert or replace (a forced recompute points the entry at the new object)."""
        with self.transaction():
            self._conn.execute(
                "INSERT OR REPLACE INTO cache (node_fp, doc_id, object_sha, kind, duration_s, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (node_fp, doc_id, object_sha, kind, duration_s, _now()),
            )

    # Runs and events
    def create_run(self, dag: str, request: Mapping[str, Any], *, scratch: bool, commit: str | None, dirty: bool) -> int:
        """Status 'queued'."""
        with self.transaction():
            return self._conn.execute(
                "INSERT INTO runs (dag, request, status, scratch, git_commit, dirty, created_at)"
                " VALUES (?, ?, 'queued', ?, ?, ?, ?)",
                (dag, _dumps(dict(request)), int(scratch), commit, int(dirty), _now()),
            ).lastrowid

    def set_run_status(self, run_id: int, status: RunStatus, error: str | None = None) -> None:
        """Sets started_at on 'running' and ended_at on terminal statuses."""
        now = _now()
        started = now if status == "running" else None
        ended = now if status in ("succeeded", "failed", "cancelled") else None
        with self.transaction():
            cur = self._conn.execute(
                "UPDATE runs SET status = ?, error = ?, started_at = COALESCE(?, started_at),"
                " ended_at = COALESCE(?, ended_at) WHERE id = ?",
                (status, error, started, ended, run_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"run {run_id!r} not found")

    def get_run(self, run_id: int) -> RunRow:
        return _run(self._get("run", "SELECT * FROM runs WHERE id = ?", run_id))

    def list_runs(self, dag: str | None = None, limit: int = 100) -> list[RunRow]:
        """Newest first."""
        if dag is None:
            rows = self._all("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,))
        else:
            rows = self._all("SELECT * FROM runs WHERE dag = ? ORDER BY id DESC LIMIT ?", (dag, limit))
        return [_run(r) for r in rows]

    def active_runs(self, dag: str | None = None) -> list[RunRow]:
        """Queued or running."""
        sql = "SELECT * FROM runs WHERE status IN ('queued', 'running')"
        if dag is None:
            rows = self._all(sql + " ORDER BY id")
        else:
            rows = self._all(sql + " AND dag = ? ORDER BY id", (dag,))
        return [_run(r) for r in rows]

    def request_cancel(self, run_id: int) -> None:
        with self.transaction():
            cur = self._conn.execute("UPDATE runs SET cancel_requested = 1 WHERE id = ?", (run_id,))
            if cur.rowcount == 0:
                raise KeyError(f"run {run_id!r} not found")

    def add_event(self, run_id: int, kind: str, payload: Mapping[str, Any]) -> int:
        with self.transaction():
            return self._conn.execute(
                "INSERT INTO run_events (run_id, ts, kind, payload) VALUES (?, ?, ?, ?)",
                (run_id, _now(), kind, _dumps(dict(payload))),
            ).lastrowid

    def events(self, run_id: int, after_id: int = 0) -> list[EventRow]:
        rows = self._all("SELECT * FROM run_events WHERE run_id = ? AND id > ? ORDER BY id", (run_id, after_id))
        return [_event(r) for r in rows]

    # Versions
    def create_version(
        self,
        *,
        dag: str,
        node: str,
        node_fp: str,
        snapshot_id: int,
        input_versions: Mapping[str, int],
        run_id: int | None,
        commit: str | None,
        dirty: bool,
        scratch: bool,
        artifact_type: str | None,
        meta: Mapping[str, Any],
    ) -> VersionRow:
        """Status 'building', seq None."""
        with self.transaction():
            version_id = self._conn.execute(
                "INSERT INTO versions (dag, node, seq, node_fp, snapshot_id, input_versions, run_id, git_commit,"
                " dirty, scratch, artifact_type, meta, stats, status, created_at)"
                " VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', 'building', ?)",
                (
                    dag,
                    node,
                    node_fp,
                    snapshot_id,
                    _dumps(dict(input_versions)),
                    run_id,
                    commit,
                    int(dirty),
                    int(scratch),
                    artifact_type,
                    _dumps(dict(meta)),
                    _now(),
                ),
            ).lastrowid
            return self.get_version(version_id)

    def add_version_docs(self, version_id: int, docs: Sequence[VersionDocInput]) -> None:
        with self.transaction():
            self._conn.executemany(
                "INSERT INTO version_docs (version_id, doc_id, status, object_sha, kind, cached, error_type,"
                " error_message, traceback, duration_s, peak_rss_mb, worker_pid)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        version_id,
                        d.doc_id,
                        d.status,
                        d.object_sha,
                        d.kind,
                        int(d.cached),
                        d.error_type,
                        d.error_message,
                        d.traceback,
                        d.duration_s,
                        d.peak_rss_mb,
                        d.worker_pid,
                    )
                    for d in docs
                ],
            )

    def finalize_version(self, version_id: int, stats: Mapping[str, Any]) -> VersionRow:
        """Status 'complete'; assigns the next seq for (dag, node)."""
        with self.transaction():
            v = self.get_version(version_id)
            if v.status != "building":
                raise ValueError(f"version {version_id} is {v.status}, not building")
            seq = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM versions WHERE dag = ? AND node = ?", (v.dag, v.node)
            ).fetchone()[0]
            self._conn.execute(
                "UPDATE versions SET status = 'complete', seq = ?, stats = ?, finalized_at = ? WHERE id = ?",
                (seq, _dumps(dict(stats)), _now(), version_id),
            )
            return self.get_version(version_id)

    def abandon_version(self, version_id: int) -> None:
        with self.transaction():
            v = self.get_version(version_id)
            if v.status != "building":
                raise ValueError(f"version {version_id} is {v.status}, not building")
            self._conn.execute("UPDATE versions SET status = 'abandoned' WHERE id = ?", (version_id,))

    def get_version(self, version_id: int) -> VersionRow:
        return _version(self._get("version", "SELECT * FROM versions WHERE id = ?", version_id))

    def find_version(self, dag: str, node: str, seq: int) -> VersionRow | None:
        row = self._one("SELECT * FROM versions WHERE dag = ? AND node = ? AND seq = ?", (dag, node, seq))
        return _version(row) if row else None

    def list_versions(self, dag: str, node: str, *, include_scratch: bool = True) -> list[VersionRow]:
        """Complete versions, newest first."""
        sql = "SELECT * FROM versions WHERE dag = ? AND node = ? AND status = 'complete'"
        if not include_scratch:
            sql += " AND scratch = 0"
        return [_version(r) for r in self._all(sql + " ORDER BY seq DESC", (dag, node))]

    def run_versions(self, run_id: int) -> list[VersionRow]:
        return [_version(r) for r in self._all("SELECT * FROM versions WHERE run_id = ? ORDER BY id", (run_id,))]

    def version_docs(self, version_id: int) -> list[VersionDocRow]:
        rows = self._all("SELECT * FROM version_docs WHERE version_id = ? ORDER BY doc_id", (version_id,))
        return [_version_doc(r) for r in rows]

    def version_doc(self, version_id: int, doc_id: str) -> VersionDocRow | None:
        row = self._one("SELECT * FROM version_docs WHERE version_id = ? AND doc_id = ?", (version_id, doc_id))
        return _version_doc(row) if row else None

    def delete_version(self, version_id: int) -> None:
        """Raises ValueError if the version is current. Objects are left in place."""
        with self.transaction():
            self.get_version(version_id)
            if self._conn.execute("SELECT 1 FROM current WHERE version_id = ?", (version_id,)).fetchone():
                raise ValueError(f"version {version_id} is current")
            self._conn.execute("DELETE FROM versions WHERE id = ?", (version_id,))

    # Current (promotion)
    def set_current(self, dag: str, node: str, version_id: int) -> None:
        with self.transaction():
            v = self.get_version(version_id)
            if (v.dag, v.node) != (dag, node):
                raise ValueError(f"version {version_id} belongs to {v.dag}/{v.node}, not {dag}/{node}")
            if v.status != "complete":
                raise ValueError(f"version {version_id} is {v.status}, not complete")
            self._conn.execute(
                "INSERT OR REPLACE INTO current (dag, node, version_id, promoted_at) VALUES (?, ?, ?, ?)",
                (dag, node, version_id, _now()),
            )

    def current_version(self, dag: str, node: str) -> VersionRow | None:
        row = self._one(
            "SELECT v.* FROM current c JOIN versions v ON v.id = c.version_id WHERE c.dag = ? AND c.node = ?",
            (dag, node),
        )
        return _version(row) if row else None

    def current_versions(self, dag: str) -> dict[str, VersionRow]:
        rows = self._all(
            "SELECT v.* FROM current c JOIN versions v ON v.id = c.version_id WHERE c.dag = ? ORDER BY c.node", (dag,)
        )
        return {r["node"]: _version(r) for r in rows}

    # Issues
    def add_issues(
        self,
        version_id: int,
        source: IssueSource,
        issues: Sequence[tuple[str | None, Issue]],
        checks_fp: str | None = None,
    ) -> None:
        with self.transaction():
            self._conn.executemany(
                "INSERT INTO version_issues (version_id, doc_id, source, checks_fp, level, code, message, page)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(version_id, doc_id, source, checks_fp, i.level, i.code, i.message, i.page) for doc_id, i in issues],
            )

    def replace_issues(
        self,
        version_id: int,
        source: IssueSource,
        issues: Sequence[tuple[str | None, Issue]],
        checks_fp: str | None = None,
    ) -> None:
        """Delete the version's issues from `source`, then add these."""
        with self.transaction():
            self._conn.execute("DELETE FROM version_issues WHERE version_id = ? AND source = ?", (version_id, source))
            self.add_issues(version_id, source, issues, checks_fp)

    def issues(self, version_id: int, doc_id: str | None = None) -> list[IssueRow]:
        if doc_id is None:
            rows = self._all("SELECT * FROM version_issues WHERE version_id = ? ORDER BY id", (version_id,))
        else:
            rows = self._all(
                "SELECT * FROM version_issues WHERE version_id = ? AND doc_id = ? ORDER BY id", (version_id, doc_id)
            )
        return [_issue(r) for r in rows]

    # Metrics
    def add_metrics(self, run_id: int, rows: Sequence[tuple[str | None, str | None, str, float]]) -> None:
        """Rows of (node, doc_id, name, value)."""
        with self.transaction():
            self._conn.executemany(
                "INSERT INTO run_metrics (run_id, node, doc_id, name, value) VALUES (?, ?, ?, ?, ?)",
                [(run_id, node, doc_id, name, float(value)) for node, doc_id, name, value in rows],
            )

    def run_metrics(self, run_id: int) -> list[MetricRow]:
        rows = self._all("SELECT * FROM run_metrics WHERE run_id = ? ORDER BY id", (run_id,))
        return [
            MetricRow(run_id=r["run_id"], node=r["node"], doc_id=r["doc_id"], name=r["name"], value=r["value"])
            for r in rows
        ]
