"""Per-doc metrics by artifact type, version summaries, and version comparison (Compare)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ingest.contract import ArtifactType
from ingest.store.catalog import Catalog, VersionDocRow
from ingest.store.objects import ObjectStore

_TOTALS: dict[str | None, tuple[str, ...]] = {
    None: ("records",),
    "parse": ("records", "pages", "ocr_pages", "tables"),
    "chunk": ("records", "chunks", "tokens"),
    "embed": ("records", "vectors"),
}


def doc_metrics(artifact_type: ArtifactType | None, record: Any) -> dict[str, float]:
    """Always "records" (1 for a dict, row count for tables). parse adds pages, ocr_pages,
    tables (count of docling tables); chunk adds chunks, tokens_mean and tokens (the sum, so
    totals over docs give tokens per chunk); embed adds vectors."""
    out: dict[str, float] = {"records": float(_records(record))}
    if artifact_type == "parse" and isinstance(record, dict):
        pages = record.get("pages") or []
        out["pages"] = float(len(pages))
        out["ocr_pages"] = float(sum(1 for p in pages if p.get("ocr")))
        out["tables"] = float(len((record.get("docling") or {}).get("tables") or []))
    elif artifact_type == "chunk":
        tokens = _column(record, "tokens")
        out["chunks"] = out["records"]
        out["tokens"] = float(sum(tokens))
        out["tokens_mean"] = sum(tokens) / len(tokens) if tokens else 0.0
    elif artifact_type == "embed":
        out["vectors"] = out["records"]
    return out


def version_summary(catalog: Catalog, objects: ObjectStore, version_id: int) -> dict[str, Any]:
    """Generic: docs, ok, failed, skipped, records, size_bytes; plus type-specific totals:
    parse pages, ocr_pages, tables; chunk chunks, tokens_per_chunk; embed vectors, model.

    Uses the version's stats (counts and "totals") when they have what's needed; computes from
    the catalog and objects otherwise."""
    v = catalog.get_version(version_id)
    atype = v.artifact_type if v.artifact_type in _TOTALS else None
    keys = _TOTALS[atype]
    stats = v.stats
    totals = stats.get("totals")
    counts = ("docs", "ok", "error", "skipped", "size_bytes")
    if isinstance(totals, Mapping) and all(k in stats for k in counts) and all(k in totals for k in keys):
        out: dict[str, Any] = {
            "docs": stats["docs"],
            "ok": stats["ok"],
            "failed": stats["error"],
            "skipped": stats["skipped"],
            "size_bytes": stats["size_bytes"],
        }
        sums = {k: totals[k] for k in keys}
    else:
        docs = catalog.version_docs(version_id)
        ok = [d for d in docs if d.status == "ok"]
        out = {
            "docs": len(docs),
            "ok": len(ok),
            "failed": sum(1 for d in docs if d.status == "error"),
            "skipped": sum(1 for d in docs if d.status == "skipped"),
            "size_bytes": sum(objects.size(d.object_sha, d.kind) for d in ok),
        }
        sums = dict.fromkeys(keys, 0.0)
        for d in ok:
            m = doc_metrics(atype, objects.get(d.object_sha, d.kind))
            for k in keys:
                sums[k] += m.get(k, 0.0)

    out["records"] = int(sums["records"])
    if atype == "parse":
        out.update(pages=int(sums["pages"]), ocr_pages=int(sums["ocr_pages"]), tables=int(sums["tables"]))
    elif atype == "chunk":
        out["chunks"] = int(sums["chunks"])
        out["tokens_per_chunk"] = sums["tokens"] / sums["chunks"] if sums["chunks"] else 0.0
    elif atype == "embed":
        out["vectors"] = int(sums["vectors"])
        out["model"] = (v.meta.get("describe") or {}).get("model")
    return out


@dataclass(frozen=True)
class DocChange:
    doc_id: str
    a_records: int | None
    """None when the doc isn't ok in version a."""
    b_records: int | None
    change: float
    """Size of change used for sorting: for tables, rows whose text_hash (or full row when there
    is no text_hash) appears in only one side; for JSON, 0 if identical else max(records)."""


@dataclass(frozen=True)
class Comparison:
    a: Mapping[str, Any]
    b: Mapping[str, Any]
    """version_summary of each side."""
    changed: tuple[DocChange, ...]
    """Docs with change > 0 or present on one side only, sorted by change descending."""
    unchanged: int


def compare_versions(catalog: Catalog, objects: ObjectStore, a_id: int, b_id: int) -> Comparison:
    """A doc present (ok) on one side only counts every record on that side as changed."""
    a_docs, b_docs = _ok_docs(catalog, a_id), _ok_docs(catalog, b_id)
    changed: list[DocChange] = []
    unchanged = 0
    for doc_id in sorted(a_docs.keys() | b_docs.keys()):
        da, db = a_docs.get(doc_id), b_docs.get(doc_id)
        ra = objects.get(da.object_sha, da.kind) if da else None
        rb = objects.get(db.object_sha, db.kind) if db else None
        na = _records(ra) if da else None
        nb = _records(rb) if db else None
        if da is None or db is None:
            changed.append(DocChange(doc_id, na, nb, float(na if da else nb)))
            continue
        if da.object_sha == db.object_sha:
            change = 0.0
        elif da.kind == "parquet" and db.kind == "parquet":
            ka, kb = _row_keys(ra), _row_keys(rb)
            sa, sb = set(ka), set(kb)
            change = float(sum(1 for k in ka if k not in sb) + sum(1 for k in kb if k not in sa))
        else:
            change = 0.0 if ra == rb else float(max(na, nb))
        if change > 0:
            changed.append(DocChange(doc_id, na, nb, change))
        else:
            unchanged += 1
    changed.sort(key=lambda c: -c.change)
    return Comparison(
        a=version_summary(catalog, objects, a_id),
        b=version_summary(catalog, objects, b_id),
        changed=tuple(changed),
        unchanged=unchanged,
    )


def doc_diff(catalog: Catalog, objects: ObjectStore, a_id: int, b_id: int, doc_id: str) -> dict[str, Any]:
    """{"a": record or None, "b": record or None} (tables as lists of dicts) plus for tables
    "only_a" / "only_b": text_hash values present on one side (in row order, deduplicated)."""
    sides = {}
    for name, version_id in (("a", a_id), ("b", b_id)):
        d = catalog.version_doc(version_id, doc_id)
        sides[name] = (d.kind, objects.get(d.object_sha, d.kind)) if d is not None and d.status == "ok" else None
    out: dict[str, Any] = {
        name: None if side is None else (side[1].to_pylist() if side[0] == "parquet" else side[1])
        for name, side in sides.items()
    }
    if any(side is not None and side[0] == "parquet" for side in sides.values()):
        ha, hb = (_hashes(sides[n]) for n in ("a", "b"))
        sa, sb = set(ha), set(hb)
        out["only_a"] = [h for h in ha if h not in sb]
        out["only_b"] = [h for h in hb if h not in sa]
    return out


def _column(record: Any, name: str) -> list[float]:
    import pyarrow as pa

    if isinstance(record, pa.Table):
        values = record.column(name).to_pylist() if name in record.column_names else []
    else:
        values = [r.get(name) for r in record]
    return [v or 0 for v in values]


def _records(record: Any) -> int:
    return 1 if isinstance(record, dict) else len(record)


def _ok_docs(catalog: Catalog, version_id: int) -> dict[str, VersionDocRow]:
    return {d.doc_id: d for d in catalog.version_docs(version_id) if d.status == "ok"}


def _row_keys(table: Any) -> list[str]:
    if "text_hash" in table.column_names:
        return [str(h) for h in table.column("text_hash").to_pylist()]
    return [json.dumps(r, sort_keys=True, default=str) for r in table.to_pylist()]


def _hashes(side: tuple[str, Any] | None) -> list[str]:
    if side is None or side[0] != "parquet" or "text_hash" not in side[1].column_names:
        return []
    return [h for h in dict.fromkeys(side[1].column("text_hash").to_pylist()) if h is not None]
