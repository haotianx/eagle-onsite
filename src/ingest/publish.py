"""The searchable vector table: load a version as one Arrow table, the library view, write
published Parquet."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ingest.sources import META_KEYS, doc_meta_columns
from ingest.store.catalog import Catalog, VersionRow
from ingest.store.objects import ObjectStore

if TYPE_CHECKING:
    import pyarrow as pa

SCHEMA_META_KEY = b"ingest"
UPLOADS_PREFIX = "uploads/"
"""Primary paths of docs in the default source's uploads folder start with this."""

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
    v = catalog.get_version(version_id)
    return _assemble([t for _, t, _ in _version_parts(catalog, objects, v)], version_metadata(catalog, version_id))


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


@dataclass(frozen=True)
class Library:
    rows: pa.Table
    """The current version's rows plus rows of docs pending against it (single-doc runs), minus
    removed docs; same columns and schema metadata as version_table."""
    documents: pa.Table
    """One row per library doc: doc_id, paths, job_number, project, subfolder, filename,
    pages (int), upload (bool: from the uploads folder), pending (bool: not in the version yet)."""
    version: dict[str, Any]
    """version_metadata of the current version."""


def library(catalog: Catalog, objects: ObjectStore, ref: str = "default/embed") -> Library:
    """The library view (Ingestion-Design §12). Rows: the current version's version_table rows,
    then those of each doc pending against that version and not ok in it (oldest first; its embed
    object joined with the pending row's paths and meta), minus removed docs; dup_count counts
    distinct docs across all of these. Documents follow the same order. pages comes from the
    parse version in the current version's lineage (pending docs: the pending row), else the max
    page in the doc's rows. Raises LookupError when `ref` (dag/node) has no current version."""
    import pyarrow as pa

    m = _REF.fullmatch(ref)
    if m is None or m["seq"] is not None:
        raise LookupError(f"invalid library ref {ref!r} (expected dag/node)")
    v = resolve_ref(catalog, ref)
    removed = catalog.removed_docs()
    parse_pages = _lineage_pages(catalog, v)

    parts: list[pa.Table] = []
    docs: list[dict[str, Any]] = []
    for doc_id, table, values in _version_parts(catalog, objects, v):
        if doc_id in removed:
            continue
        parts.append(table)
        docs.append(_document(doc_id, values, parse_pages.get(doc_id), table, pending=False))
    in_version = {d.doc_id for d in catalog.version_docs(v.id) if d.status == "ok"}
    for p in catalog.pending_docs(v.id):
        if p.doc_id in in_version or p.doc_id in removed:
            continue
        values = _meta_values(p.paths, p.meta)
        table = _with_meta(_doc_table(objects, p.object_sha, p.doc_id, v), values)
        parts.append(table)
        docs.append(_document(p.doc_id, values, p.pages, table, pending=True))

    meta = version_metadata(catalog, v.id)
    documents = pa.Table.from_pylist(docs, schema=_documents_schema())
    return Library(rows=_assemble(parts, meta), documents=documents, version=meta)


def load_library(ref: str = "default/embed", *, repo_root: Path | None = None) -> Library:
    """Public API (`ingest.load_library`): open the repo's config and catalog and return
    library(ref)."""
    from ingest.config import load_config

    config = load_config(repo_root)
    catalog = Catalog(config.catalog_path)
    try:
        return library(catalog, ObjectStore(config.objects_dir), ref)
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


def _documents_schema() -> pa.Schema:
    import pyarrow as pa

    extra = [("pages", pa.int64()), ("upload", pa.bool_()), ("pending", pa.bool_())]
    return pa.schema([("doc_id", pa.string()), *_meta_schema(), *extra])


def _meta_values(paths: Sequence[str], meta: Mapping[str, Any]) -> dict[str, Any]:
    return {"paths": list(paths), **{k: (None if meta.get(k) is None else str(meta[k])) for k in META_KEYS}}


def _doc_table(objects: ObjectStore, object_sha: str, doc_id: str, v: VersionRow) -> pa.Table:
    t = objects.get_table(object_sha).replace_schema_metadata(None)
    clash = sorted(set(t.column_names) & {"paths", *META_KEYS, "dup_count"})
    if clash:
        raise ValueError(f"doc {doc_id} in {v.label} already has columns {clash}")
    return t


def _version_parts(
    catalog: Catalog, objects: ObjectStore, v: VersionRow
) -> list[tuple[str, pa.Table, dict[str, Any]]]:
    """(doc_id, table with metadata columns, metadata values) per ok doc in snapshot order.
    Raises ValueError for JSON-kind versions."""
    ok = {d.doc_id: d for d in catalog.version_docs(v.id) if d.status == "ok"}
    json_docs = [d.doc_id for d in ok.values() if d.kind != "parquet"]
    if json_docs:
        raise ValueError(f"{v.label} has JSON records (e.g. doc {json_docs[0]}); only table versions load as a table")
    out: list[tuple[str, pa.Table, dict[str, Any]]] = []
    for row in catalog.snapshot_docs(v.snapshot_id):
        d = ok.get(row.doc_id)
        if d is None:
            continue
        cols = doc_meta_columns(row)
        values = _meta_values(cols["paths"], cols)
        table = _with_meta(_doc_table(objects, d.object_sha, row.doc_id, v), values)
        out.append((row.doc_id, table, values))
    return out


def _assemble(parts: Sequence[pa.Table], metadata: Mapping[str, Any]) -> pa.Table:
    """Concatenate, add dup_count (distinct docs per text_hash) and the schema metadata."""
    import pyarrow as pa

    if parts:
        table = pa.concat_tables(parts, promote_options="default")
    else:
        table = pa.Table.from_pylist([], schema=_meta_schema())
    if "text_hash" in table.column_names:
        hashes: Counter[str] = Counter()
        for t in parts:
            if "text_hash" in t.column_names:
                hashes.update(h for h in set(t.column("text_hash").to_pylist()) if h is not None)
        dup = [None if h is None else hashes[h] for h in table.column("text_hash").to_pylist()]
        table = table.append_column("dup_count", pa.array(dup, pa.int64()))
    meta = json.dumps(dict(metadata), sort_keys=True, ensure_ascii=False)
    return table.replace_schema_metadata({SCHEMA_META_KEY: meta.encode("utf-8")})


def _lineage_pages(catalog: Catalog, v: VersionRow) -> dict[str, int]:
    """doc_id → pages from the nearest "parse" version among `v`'s inputs (breadth first)."""
    queue, seen = [v], {v.id}
    while queue:
        cur = queue.pop(0)
        if cur.artifact_type == "parse":
            docs = catalog.version_docs(cur.id)
            return {d.doc_id: int(d.metrics["pages"]) for d in docs if d.metrics.get("pages") is not None}
        for vid in cur.input_versions.values():
            if vid not in seen:
                seen.add(vid)
                queue.append(catalog.get_version(vid))
    return {}


def _document(
    doc_id: str, values: Mapping[str, Any], pages: int | None, table: pa.Table, *, pending: bool
) -> dict[str, Any]:
    """A documents-table row; `pages` falls back to the max page in the doc's rows."""
    if pages is None and "pages" in table.column_names:
        pages = max((p for ps in table.column("pages").to_pylist() for p in ps or () if p is not None), default=None)
    paths = values["paths"]
    upload = bool(paths) and paths[0].startswith(UPLOADS_PREFIX)
    return {"doc_id": doc_id, **values, "pages": pages, "upload": upload, "pending": pending}


def _with_meta(table: pa.Table, values: Mapping[str, Any]) -> pa.Table:
    import pyarrow as pa

    n = table.num_rows
    for f in _meta_schema():
        table = table.append_column(f, pa.array([values[f.name]] * n, f.type))
    return table
