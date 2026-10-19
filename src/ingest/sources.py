"""Sync a source folder into an immutable snapshot."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ingest.config import SourceConfig
from ingest.contract import Doc
from ingest.store.catalog import Catalog, SnapshotDocInput, SnapshotDocRow

META_KEYS = ("job_number", "project", "subfolder", "filename")


def parse_path_meta(rel_path: str) -> dict[str, Any]:
    """Path-derived metadata from a source-relative path such as
    "2292 SZEKERES/CONTRACT/AEROTECH/2292 Szekeres Proposal.pdf":
        job_number  leading job id of the first component ("2292", "1934-03"), else None
        project     rest of the first component ("SZEKERES"; "4476 - Lewis - Via Rancho P3" →
                    "Lewis - Via Rancho P3"), else the whole component
        subfolder   components between the first and the file ("CONTRACT/AEROTECH"), else None
        filename    the file name
    """
    raise NotImplementedError


def check_pdf(path: Path) -> tuple[tuple[str, ...], str | None]:
    """Open with pypdfium2 and return (flags, reason): ("empty",) for 0-byte or 0-page files,
    ("encrypted",) for password-protected, ("corrupt",) when it can't be opened; () and None when
    fine. Never raises for bad files."""
    raise NotImplementedError


def scan_source(source: SourceConfig, catalog: Catalog | None = None) -> tuple[list[SnapshotDocInput], int]:
    """Walk source.root for files matching source.glob (suffix compared case-insensitively),
    hash them (reusing catalog.file_index_get entries whose size and mtime_ns match), check
    PDFs, and collapse identical files into one doc whose `paths` are sorted. meta comes from
    the primary (first) path. Updates the file index when `catalog` is given. Returns
    (docs sorted by primary path, number of files)."""
    raise NotImplementedError


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
    raise NotImplementedError


def sync(catalog: Catalog, source: SourceConfig) -> SyncResult:
    """Scan and create a snapshot if anything differs from the latest one (doc ids, paths,
    flags); otherwise return the latest with created=False and an empty diff."""
    raise NotImplementedError


def diff_snapshots(catalog: Catalog, old_id: int, new_id: int) -> SnapshotDiff:
    raise NotImplementedError


def to_doc(row: SnapshotDocRow, source: SourceConfig) -> Doc:
    """contract.Doc for a snapshot row, with file = source.root / paths[0]."""
    raise NotImplementedError


def snapshot_docs(catalog: Catalog, snapshot_id: int, source: SourceConfig) -> list[Doc]:
    raise NotImplementedError


def doc_meta_columns(row: SnapshotDocRow) -> Mapping[str, Any]:
    """Columns joined onto published tables: paths plus META_KEYS."""
    raise NotImplementedError
