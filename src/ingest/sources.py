"""Sync a source folder into an immutable snapshot."""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from ingest.config import SourceConfig
from ingest.contract import Doc
from ingest.fingerprint import sha256_file
from ingest.store.catalog import Catalog, SnapshotDocInput, SnapshotDocRow

META_KEYS = ("job_number", "project", "subfolder", "filename")

_JOB = re.compile(r"^(\d+(?:-\d+)*)(?:\s*-\s*|\s+|$)(.*)$")


def parse_path_meta(rel_path: str) -> dict[str, Any]:
    """Path-derived metadata from a source-relative path such as
    "2292 SZEKERES/CONTRACT/AEROTECH/2292 Szekeres Proposal.pdf":
        job_number  leading job id of the first component ("2292", "1934-03"), else None
        project     rest of the first component ("SZEKERES"; "4476 - Lewis - Via Rancho P3" →
                    "Lewis - Via Rancho P3"), else the whole component
        subfolder   components between the first and the file ("CONTRACT/AEROTECH"), else None
        filename    the file name
    """
    parts = PurePosixPath(rel_path).parts
    filename = parts[-1] if parts else None
    if len(parts) < 2:
        return {"job_number": None, "project": None, "subfolder": None, "filename": filename}
    first = parts[0]
    m = _JOB.match(first)
    job_number, project = (m.group(1), m.group(2) or None) if m else (None, first)
    subfolder = "/".join(parts[1:-1]) or None
    return {"job_number": job_number, "project": project, "subfolder": subfolder, "filename": filename}


def check_pdf(path: Path) -> tuple[tuple[str, ...], str | None]:
    """Open with pypdfium2 and return (flags, reason): ("empty",) for 0-byte or 0-page files,
    ("encrypted",) for password-protected, ("corrupt",) when it can't be opened; () and None when
    fine. Never raises for bad files."""
    try:
        if path.stat().st_size == 0:
            return ("empty",), "0-byte file"
        pdf = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as e:
        if e.err_code == pdfium_c.FPDF_ERR_PASSWORD:
            return ("encrypted",), "password required"
        return ("corrupt",), str(e)
    except Exception as e:
        return ("corrupt",), f"{type(e).__name__}: {e}"
    try:
        if len(pdf) == 0:
            return ("empty",), "0 pages"
    finally:
        pdf.close()
    return (), None


def scan_source(source: SourceConfig, catalog: Catalog | None = None) -> tuple[list[SnapshotDocInput], int]:
    """Walk source.root for files matching source.glob (suffix compared case-insensitively),
    hash them (reusing catalog.file_index_get entries whose size and mtime_ns match), check
    PDFs, and collapse identical files into one doc whose `paths` are sorted. meta comes from
    the primary (first) path. Updates the file index when `catalog` is given. Returns
    (docs sorted by primary path, number of files)."""
    root = source.root
    if not root.is_dir():
        raise FileNotFoundError(f"source {source.name!r}: {root} is not a directory")
    index = catalog.file_index_get(source.name) if catalog is not None else {}
    entries: dict[str, tuple[int, int, str]] = {}
    for rel in _walk(root, source.glob):
        st = (root / rel).stat()
        cached = index.get(rel)
        if cached is not None and cached[0] == st.st_size and cached[1] == st.st_mtime_ns:
            sha = cached[2]
        else:
            sha = sha256_file(root / rel)
        entries[rel] = (st.st_size, st.st_mtime_ns, sha)
    if catalog is not None:
        catalog.file_index_put(source.name, entries)

    groups: dict[str, list[str]] = {}
    for rel, (_, _, sha) in entries.items():
        groups.setdefault(sha, []).append(rel)
    docs = []
    for sha, paths in groups.items():
        paths.sort()
        primary = paths[0]
        size, mtime_ns, _ = entries[primary]
        flags, reason = check_pdf(root / primary) if primary.lower().endswith(".pdf") else ((), None)
        docs.append(
            SnapshotDocInput(
                doc_id=sha,
                paths=tuple(paths),
                size=size,
                mtime_ns=mtime_ns,
                meta=parse_path_meta(primary),
                flags=flags,
                flag_reason=reason,
            )
        )
    docs.sort(key=lambda d: d.paths[0])
    return docs, len(entries)


def _walk(root: Path, glob: str) -> Iterator[str]:
    """Source-relative POSIX paths of non-hidden files matching `glob` (case-insensitive)."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        base = PurePosixPath(Path(dirpath).relative_to(root).as_posix())
        for name in filenames:
            if name.startswith("."):
                continue
            rel = base / name
            if rel.full_match(glob, case_sensitive=False) and os.path.isfile(os.path.join(dirpath, name)):
                yield rel.as_posix()


@dataclass(frozen=True)
class SnapshotDiff:
    added: tuple[str, ...]
    """Doc ids new in the later snapshot."""
    removed: tuple[str, ...]
    """Doc ids gone from the later snapshot."""
    changed_paths: tuple[str, ...]
    """Paths present in both whose content (doc id) differs."""
    unchanged: int

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed_paths)


@dataclass(frozen=True)
class SyncResult:
    snapshot_id: int
    created: bool
    """False when contents matched the latest snapshot, which is returned instead."""
    diff: SnapshotDiff


def diff_docs(old: Sequence[SnapshotDocRow | SnapshotDocInput], new: Sequence[SnapshotDocRow | SnapshotDocInput]) -> SnapshotDiff:
    old_ids = {d.doc_id for d in old}
    new_ids = {d.doc_id for d in new}
    old_by_path = {p: d.doc_id for d in old for p in d.paths}
    new_by_path = {p: d.doc_id for d in new for p in d.paths}
    return SnapshotDiff(
        added=tuple(sorted(new_ids - old_ids)),
        removed=tuple(sorted(old_ids - new_ids)),
        changed_paths=tuple(sorted(p for p in old_by_path.keys() & new_by_path.keys() if old_by_path[p] != new_by_path[p])),
        unchanged=len(old_ids & new_ids),
    )


def _contents(docs: Sequence[SnapshotDocRow | SnapshotDocInput]) -> set[tuple[str, tuple[str, ...], tuple[str, ...]]]:
    return {(d.doc_id, tuple(d.paths), tuple(d.flags)) for d in docs}


def sync(catalog: Catalog, source: SourceConfig) -> SyncResult:
    """Scan and create a snapshot if anything differs from the latest one (doc ids, paths,
    flags); otherwise return the latest with created=False and an empty diff."""
    docs, file_count = scan_source(source, catalog)
    latest = catalog.latest_snapshot(source.name)
    old = catalog.snapshot_docs(latest.id) if latest is not None else []
    if latest is not None and _contents(old) == _contents(docs):
        return SyncResult(latest.id, False, SnapshotDiff((), (), (), len(old)))
    snapshot_id = catalog.create_snapshot(source.name, docs, file_count)
    return SyncResult(snapshot_id, True, diff_docs(old, docs))


def diff_snapshots(catalog: Catalog, old_id: int, new_id: int) -> SnapshotDiff:
    return diff_docs(catalog.snapshot_docs(old_id), catalog.snapshot_docs(new_id))


def to_doc(row: SnapshotDocRow, source: SourceConfig) -> Doc:
    """contract.Doc for a snapshot row, with file = source.root / paths[0]."""
    return Doc(
        id=row.doc_id,
        paths=row.paths,
        size=row.size,
        file=source.root / row.paths[0],
        meta=dict(row.meta),
        flags=row.flags,
    )


def snapshot_docs(catalog: Catalog, snapshot_id: int, source: SourceConfig) -> list[Doc]:
    return [to_doc(r, source) for r in catalog.snapshot_docs(snapshot_id)]


def doc_meta_columns(row: SnapshotDocRow) -> Mapping[str, Any]:
    """Columns joined onto published tables: paths plus META_KEYS."""
    return {"paths": list(row.paths), **{k: row.meta.get(k) for k in META_KEYS}}
