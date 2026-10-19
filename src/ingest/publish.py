"""The searchable vector table: load a version as one Arrow table, write published Parquet."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ingest.store.catalog import Catalog, VersionRow
from ingest.store.objects import ObjectStore

if TYPE_CHECKING:
    import pyarrow as pa

SCHEMA_META_KEY = b"ingest"


def resolve_ref(catalog: Catalog, ref: str) -> VersionRow:
    """"dag/node" → current version; "dag/node@v3" → seq 3. Raises LookupError."""
    raise NotImplementedError


def version_metadata(catalog: Catalog, version_id: int) -> dict[str, Any]:
    """dag, node, version (label), seq, snapshot_id, node_fp, commit, scratch, created_at, plus
    the version's describe() output (meta["describe"])."""
    raise NotImplementedError


def version_table(catalog: Catalog, objects: ObjectStore, version_id: int) -> pa.Table:
    """Concatenate the Parquet objects of the version's ok docs (in snapshot order) and join, by
    doc_id, the snapshot's doc metadata (sources.doc_meta_columns: paths, job_number, project,
    subfolder, filename). When a text_hash column exists, add dup_count = number of distinct
    docs in this version sharing that text_hash. version_metadata() is stored as JSON under
    SCHEMA_META_KEY in the schema metadata. Raises ValueError for JSON-kind versions."""
    raise NotImplementedError


def load_table(ref: str, *, repo_root: Path | None = None) -> pa.Table:
    """Public API (`ingest.load_table`): open the repo's config and catalog and return
    version_table for `ref`."""
    raise NotImplementedError


def write_published(catalog: Catalog, objects: ObjectStore, version_id: int, published_dir: Path) -> Path:
    """Atomically write version_table to published_dir/<dag>/<node>.parquet; returns the path."""
    raise NotImplementedError
