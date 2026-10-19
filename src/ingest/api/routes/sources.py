"""Sources, snapshots, Sync, snapshot diffs and docs."""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter

from ingest import sources
from ingest.api.deps import CatalogDep, ConfigDep, bad_request, not_found, snapshot_or_404
from ingest.api.models import DocRef, SnapshotDiffOut, SnapshotDocOut, SourceOut, SyncOut
from ingest.api.views import doc_fields, matches, snapshot_out
from ingest.config import SourceConfig
from ingest.store.catalog import Catalog, SnapshotDocRow

router = APIRouter(tags=["sources"])


def folders(source: SourceConfig) -> list[str]:
    return [str(f) for f in getattr(source, "folders", None) or [source.root]]


def _ref(row: SnapshotDocRow) -> DocRef:
    fields = doc_fields(row)
    return DocRef(
        doc_id=row.doc_id,
        path=fields["path"] or "",
        filename=fields["filename"],
        job_number=fields["job_number"],
        project=fields["project"],
    )


def _diff(catalog: Catalog, old_id: int | None, new_id: int) -> SnapshotDiffOut:
    new = snapshot_or_404(catalog, new_id)
    old = snapshot_or_404(catalog, old_id) if old_id is not None else None
    new_rows = catalog.snapshot_docs(new.id)
    old_rows: Sequence[SnapshotDocRow] = catalog.snapshot_docs(old.id) if old else []
    diff = sources.diff_docs(old_rows, new_rows)
    added, removed = set(diff.added), set(diff.removed)
    return SnapshotDiffOut(
        old=snapshot_out(old) if old else None,
        new=snapshot_out(new),
        added=[_ref(r) for r in new_rows if r.doc_id in added],
        removed=[_ref(r) for r in old_rows if r.doc_id in removed],
        changed_paths=list(diff.changed_paths),
        unchanged=diff.unchanged,
    )


def _previous(catalog: Catalog, snapshot_id: int) -> int | None:
    source = snapshot_or_404(catalog, snapshot_id).source
    return next((s.id for s in catalog.list_snapshots(source) if s.id < snapshot_id), None)


@router.get("/sources")
def list_sources(config: ConfigDep, catalog: CatalogDep) -> list[SourceOut]:
    return [
        SourceOut(
            name=name,
            folders=folders(source),
            glob=source.glob,
            snapshots=[snapshot_out(s) for s in catalog.list_snapshots(name)],
        )
        for name, source in config.sources.items()
    ]


@router.post("/sources/{name}/sync")
def sync_source(name: str, config: ConfigDep, catalog: CatalogDep) -> SyncOut:
    source = config.sources.get(name)
    if source is None:
        raise not_found(f"Source {name!r} not found")
    try:
        result = sources.sync(catalog, source)
    except FileNotFoundError as e:
        raise bad_request(str(e)) from None
    old_id = _previous(catalog, result.snapshot_id) if result.created else result.snapshot_id
    return SyncOut(
        snapshot=snapshot_out(catalog.get_snapshot(result.snapshot_id)),
        created=result.created,
        diff=_diff(catalog, old_id, result.snapshot_id),
    )


@router.get("/snapshots/{snapshot_id}/diff")
def snapshot_diff(snapshot_id: int, catalog: CatalogDep, against: int | None = None) -> SnapshotDiffOut:
    """Diff from `against` (default: the previous snapshot of the same source) to this one."""
    old_id = against if against is not None else _previous(catalog, snapshot_id)
    return _diff(catalog, old_id, snapshot_id)


@router.get("/snapshots/{snapshot_id}/docs")
def snapshot_docs(snapshot_id: int, catalog: CatalogDep, q: str | None = None, limit: int = 200) -> list[SnapshotDocOut]:
    """`q` matches filename, path or job number (case-insensitive substring)."""
    snapshot_or_404(catalog, snapshot_id)
    rows = [r for r in catalog.snapshot_docs(snapshot_id) if matches(r, q)][: max(limit, 0)]
    return [
        SnapshotDocOut(
            **_ref(r).model_dump(), paths=list(r.paths), size=r.size, flags=list(r.flags), flag_reason=r.flag_reason
        )
        for r in rows
    ]
