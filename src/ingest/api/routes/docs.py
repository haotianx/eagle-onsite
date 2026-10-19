"""Original PDFs."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from ingest.api.deps import CatalogDep, ConfigDep, not_found
from ingest.config import Config
from ingest.fingerprint import sha256_file
from ingest.store.catalog import Catalog, SnapshotDocRow

router = APIRouter(tags=["docs"])

UPLOADS_PREFIX = "uploads/"
CHANGED = "Original changed or missing"


def _or_empty[T](call: Callable[[], T], empty: T) -> T:
    """Pending/removed docs are Phase 1 stubs until merged; treat them as none meanwhile."""
    try:
        return call()
    except NotImplementedError:
        return empty


def original_path(config: Config, root: Path | None, path: str) -> Path | None:
    """A snapshot path on disk: uploads/<file> under the data dir, others under the source root."""
    if path.startswith(UPLOADS_PREFIX):
        return config.data_dir / path
    return root / path if root is not None else None


def _snapshot_row(config: Config, catalog: Catalog, doc_id: str) -> tuple[SnapshotDocRow, Path | None] | None:
    """The doc in the latest snapshot of a source, else in any snapshot (newest first)."""
    roots = {name: s.root for name, s in config.sources.items()}
    for name in config.sources:
        latest = catalog.latest_snapshot(name)
        if latest is not None and (row := catalog.snapshot_doc(latest.id, doc_id)) is not None:
            return row, roots[name]
    for snapshot in catalog.list_snapshots():
        if (row := catalog.snapshot_doc(snapshot.id, doc_id)) is not None:
            return row, roots.get(snapshot.source)
    return None


def _unchanged(path: Path, doc_id: str, size: int | None, mtime_ns: int | None) -> bool:
    if not path.is_file():
        return False
    st = path.stat()
    if size is not None and st.st_size == size and st.st_mtime_ns == mtime_ns:
        return True
    return sha256_file(path) == doc_id


@router.get("/docs/{doc_id}/pdf", response_class=FileResponse)
def doc_pdf(doc_id: str, config: ConfigDep, catalog: CatalogDep) -> FileResponse:
    """The document's original file. 410 "Document removed" for removed uploads; 404 "Original
    changed or missing" when no file with this content exists any more."""
    if doc_id in _or_empty(catalog.removed_docs, set()):
        raise HTTPException(status.HTTP_410_GONE, "Document removed")
    found = _snapshot_row(config, catalog, doc_id)
    if found is not None:
        row, root = found
        path = original_path(config, root, row.paths[0]) if row.paths else None
        size, mtime_ns = row.size, row.mtime_ns
    else:
        pending = next((p for p in _or_empty(catalog.pending_docs, []) if p.doc_id == doc_id), None)
        if pending is None:
            raise not_found(f"Document {doc_id!r} not found")
        root = next(iter(config.sources.values())).root if config.sources else None
        path = original_path(config, root, pending.paths[0]) if pending.paths else None
        size = mtime_ns = None
    if path is None or not _unchanged(path, doc_id, size, mtime_ns):
        raise not_found(CHANGED)
    return FileResponse(path, media_type="application/pdf", filename=path.name, content_disposition_type="inline")
