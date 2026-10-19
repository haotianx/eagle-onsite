"""SQLite catalog: snapshots, file index, cache index, runs, events, versions, current, issues,
metrics. Schema lives in schema.sql and is created on open.

Concurrency: WAL mode. Many readers (API, CLI); writes come from one process per run (the
executor parent) plus short CLI/API actions (promote, cancel). Use a busy timeout. JSON columns are
stored with fingerprint.canonical_json and decoded on read. Timestamps are UTC ISO-8601 strings.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypeAlias

from ingest.contract import Issue

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


class Catalog:
    def __init__(self, path: Path) -> None:
        """Open (creating parent dirs and schema if needed)."""
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group several writes atomically. Methods called inside don't commit on their own."""
        raise NotImplementedError
        yield

    # Snapshots
    def create_snapshot(self, source: str, docs: Sequence[SnapshotDocInput], file_count: int) -> int:
        raise NotImplementedError

    def latest_snapshot(self, source: str) -> SnapshotRow | None:
        raise NotImplementedError

    def get_snapshot(self, snapshot_id: int) -> SnapshotRow:
        """Raises KeyError if missing (all get_* methods do)."""
        raise NotImplementedError

    def list_snapshots(self, source: str | None = None) -> list[SnapshotRow]:
        """Newest first."""
        raise NotImplementedError

    def snapshot_docs(self, snapshot_id: int) -> list[SnapshotDocRow]:
        """Ordered by primary path."""
        raise NotImplementedError

    def snapshot_doc(self, snapshot_id: int, doc_id: str) -> SnapshotDocRow | None:
        raise NotImplementedError

    # File index (lets sync skip re-hashing unchanged files)
    def file_index_get(self, source: str) -> dict[str, tuple[int, int, str]]:
        """rel_path → (size, mtime_ns, sha)."""
        raise NotImplementedError

    def file_index_put(self, source: str, entries: Mapping[str, tuple[int, int, str]]) -> None:
        """Replace the source's index with `entries`."""
        raise NotImplementedError

    # Cache index
    def cache_get(self, node_fp: str, doc_id: str) -> CacheEntry | None:
        raise NotImplementedError

    def cache_get_many(self, node_fp: str, doc_ids: Sequence[str]) -> dict[str, CacheEntry]:
        raise NotImplementedError

    def cache_put(self, node_fp: str, doc_id: str, object_sha: str, kind: str, duration_s: float | None) -> None:
        """Insert or replace (a forced recompute points the entry at the new object)."""
        raise NotImplementedError

    # Runs and events
    def create_run(self, dag: str, request: Mapping[str, Any], *, scratch: bool, commit: str | None, dirty: bool) -> int:
        """Status 'queued'."""
        raise NotImplementedError

    def set_run_status(self, run_id: int, status: RunStatus, error: str | None = None) -> None:
        """Sets started_at on 'running' and ended_at on terminal statuses."""
        raise NotImplementedError

    def get_run(self, run_id: int) -> RunRow:
        raise NotImplementedError

    def list_runs(self, dag: str | None = None, limit: int = 100) -> list[RunRow]:
        """Newest first."""
        raise NotImplementedError

    def active_runs(self, dag: str | None = None) -> list[RunRow]:
        """Queued or running."""
        raise NotImplementedError

    def request_cancel(self, run_id: int) -> None:
        raise NotImplementedError

    def add_event(self, run_id: int, kind: str, payload: Mapping[str, Any]) -> int:
        raise NotImplementedError

    def events(self, run_id: int, after_id: int = 0) -> list[EventRow]:
        raise NotImplementedError

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
        raise NotImplementedError

    def add_version_docs(self, version_id: int, docs: Sequence[VersionDocInput]) -> None:
        raise NotImplementedError

    def finalize_version(self, version_id: int, stats: Mapping[str, Any]) -> VersionRow:
        """Status 'complete'; assigns the next seq for (dag, node)."""
        raise NotImplementedError

    def abandon_version(self, version_id: int) -> None:
        raise NotImplementedError

    def get_version(self, version_id: int) -> VersionRow:
        raise NotImplementedError

    def find_version(self, dag: str, node: str, seq: int) -> VersionRow | None:
        raise NotImplementedError

    def list_versions(self, dag: str, node: str, *, include_scratch: bool = True) -> list[VersionRow]:
        """Complete versions, newest first."""
        raise NotImplementedError

    def run_versions(self, run_id: int) -> list[VersionRow]:
        raise NotImplementedError

    def version_docs(self, version_id: int) -> list[VersionDocRow]:
        raise NotImplementedError

    def version_doc(self, version_id: int, doc_id: str) -> VersionDocRow | None:
        raise NotImplementedError

    def delete_version(self, version_id: int) -> None:
        """Raises ValueError if the version is current. Objects are left in place."""
        raise NotImplementedError

    # Current (promotion)
    def set_current(self, dag: str, node: str, version_id: int) -> None:
        raise NotImplementedError

    def current_version(self, dag: str, node: str) -> VersionRow | None:
        raise NotImplementedError

    def current_versions(self, dag: str) -> dict[str, VersionRow]:
        raise NotImplementedError

    # Issues
    def add_issues(
        self,
        version_id: int,
        source: IssueSource,
        issues: Sequence[tuple[str | None, Issue]],
        checks_fp: str | None = None,
    ) -> None:
        raise NotImplementedError

    def replace_issues(
        self,
        version_id: int,
        source: IssueSource,
        issues: Sequence[tuple[str | None, Issue]],
        checks_fp: str | None = None,
    ) -> None:
        """Delete the version's issues from `source`, then add these."""
        raise NotImplementedError

    def issues(self, version_id: int, doc_id: str | None = None) -> list[IssueRow]:
        raise NotImplementedError

    # Metrics
    def add_metrics(self, run_id: int, rows: Sequence[tuple[str | None, str | None, str, float]]) -> None:
        """Rows of (node, doc_id, name, value)."""
        raise NotImplementedError

    def run_metrics(self, run_id: int) -> list[MetricRow]:
        raise NotImplementedError
