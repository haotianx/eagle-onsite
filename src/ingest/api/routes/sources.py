"""Sources, snapshots, Sync, snapshot diffs and docs."""

from __future__ import annotations

from fastapi import APIRouter

from ingest.api.models import SnapshotDiffOut, SnapshotDocOut, SourceOut, SyncOut

router = APIRouter(tags=["sources"])


@router.get("/sources")
def list_sources() -> list[SourceOut]:
    raise NotImplementedError


@router.post("/sources/{name}/sync")
def sync_source(name: str) -> SyncOut:
    raise NotImplementedError


@router.get("/snapshots/{snapshot_id}/diff")
def snapshot_diff(snapshot_id: int, against: int | None = None) -> SnapshotDiffOut:
    """Diff from `against` (default: the previous snapshot of the same source) to this one."""
    raise NotImplementedError


@router.get("/snapshots/{snapshot_id}/docs")
def snapshot_docs(snapshot_id: int, q: str | None = None, limit: int = 200) -> list[SnapshotDocOut]:
    """`q` matches filename, path or job number (case-insensitive substring)."""
    raise NotImplementedError
