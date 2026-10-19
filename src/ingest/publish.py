"""The searchable vector table: load a version as one Arrow table, write published Parquet."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ingest.sources import META_KEYS, doc_meta_columns
from ingest.store.catalog import Catalog, SnapshotDocRow, VersionRow
from ingest.store.objects import ObjectStore

if TYPE_CHECKING:
    import pyarrow as pa

SCHEMA_META_KEY = b"ingest"

_REF = re.compile(r"(?P<dag>[^/@]+)/(?P<node>[^/@]+)(?:@v(?P<seq>[0-9]+))?")


def resolve_ref(catalog: Catalog, ref: str) -> VersionRow:
    """"dag/node" → current version; "dag/node@v3" → seq 3. Raises LookupError."""
    m = _REF.fullmatch(ref)
    if m is None:
        raise LookupError(f"invalid version ref {ref!r} (expected dag/node or dag/node@vN)")
    dag, node, seq = m["dag"], m["node"], m["seq"]
    if seq is None:
        v = catalog.current_version(dag, node)
        if v is None:
            raise LookupError(f"{dag}/{node} has no current version")
        return v
    v = catalog.find_version(dag, node, int(seq))
    if v is None:
        raise LookupError(f"version {ref!r} not found")
    return v


def version_metadata(catalog: Catalog, version_id: int) -> dict[str, Any]:
    """dag, node, version (label), seq, snapshot_id, node_fp, commit, scratch, created_at, plus
    the version's describe() output (meta["describe"]), merged at the top level (the framework keys
    win on a name clash)."""
    v = catalog.get_version(version_id)
    return {
        **dict(v.meta.get("describe") or {}),
        "dag": v.dag,
        "node": v.node,
        "version": v.label,
        "seq": v.seq,
        "snapshot_id": v.snapshot_id,
        "node_fp": v.node_fp,
        "commit": v.commit,
        "scratch": v.scratch,
        "created_at": v.created_at,
    }


def version_table(catalog: Catalog, objects: ObjectStore, version_id: int) -> pa.Table:
    """Concatenate the Parquet objects of the version's ok docs (in snapshot order) and join, by
    doc_id, the snapshot's doc metadata (sources.doc_meta_columns: paths, job_number, project,
    subfolder, filename). When a text_hash column exists, add dup_count = number of distinct
    docs in this version sharing that text_hash. version_metadata() is stored as JSON under
    SCHEMA_META_KEY in the schema metadata. Raises ValueError for JSON-kind versions.

    A version with no ok docs yields an empty table holding only the metadata columns. Raises
    ValueError when a doc's table already has a metadata column."""
    import pyarrow as pa

    v = catalog.get_version(version_id)
    ok = {d.doc_id: d for d in catalog.version_docs(version_id) if d.status == "ok"}
    json_docs = [d.doc_id for d in ok.values() if d.kind != "parquet"]
    if json_docs:
        raise ValueError(f"{v.label} has JSON records (e.g. doc {json_docs[0]}); only table versions load as a table")

    parts: list[pa.Table] = []
    hashes: Counter[str] = Counter()
    for row in catalog.snapshot_docs(v.snapshot_id):
        d = ok.get(row.doc_id)
        if d is None:
            continue
        t = objects.get_table(d.object_sha).replace_schema_metadata(None)
        clash = sorted(set(t.column_names) & {"paths", *META_KEYS, "dup_count"})
        if clash:
            raise ValueError(f"doc {row.doc_id} in {v.label} already has columns {clash}")
        if "text_hash" in t.column_names:
            hashes.update(h for h in set(t.column("text_hash").to_pylist()) if h is not None)
        parts.append(_with_meta(t, row))

    if parts:
        table = pa.concat_tables(parts, promote_options="default")
    else:
        table = pa.Table.from_pylist([], schema=_meta_schema())
    if "text_hash" in table.column_names:
        dup = [None if h is None else hashes[h] for h in table.column("text_hash").to_pylist()]
        table = table.append_column("dup_count", pa.array(dup, pa.int64()))
    meta = json.dumps(version_metadata(catalog, version_id), sort_keys=True, ensure_ascii=False)
    return table.replace_schema_metadata({SCHEMA_META_KEY: meta.encode("utf-8")})


def load_table(ref: str, *, repo_root: Path | None = None) -> pa.Table:
    """Public API (`ingest.load_table`): open the repo's config and catalog and return
    version_table for `ref`."""
    from ingest.config import load_config

    config = load_config(repo_root)
    catalog = Catalog(config.catalog_path)
    try:
        v = resolve_ref(catalog, ref)
        return version_table(catalog, ObjectStore(config.objects_dir), v.id)
    finally:
        catalog.close()


def write_published(catalog: Catalog, objects: ObjectStore, version_id: int, published_dir: Path) -> Path:
    """Atomically write version_table to published_dir/<dag>/<node>.parquet; returns the path."""
    import pyarrow.parquet as pq

    v = catalog.get_version(version_id)
    table = version_table(catalog, objects, version_id)
    dest = Path(published_dir) / v.dag / f"{v.node}.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{v.node}.", suffix=".tmp")
    os.close(fd)
    try:
        pq.write_table(table, tmp)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return dest


def _meta_schema() -> pa.Schema:
    import pyarrow as pa

    return pa.schema([("paths", pa.list_(pa.string())), *((k, pa.string()) for k in META_KEYS)])


def _with_meta(table: pa.Table, row: SnapshotDocRow) -> pa.Table:
    import pyarrow as pa

    n = table.num_rows
    values = {k: (v if k == "paths" or v is None else str(v)) for k, v in doc_meta_columns(row).items()}
    for f in _meta_schema():
        table = table.append_column(f, pa.array([values[f.name]] * n, f.type))
    return table
