"""Compare two versions of an artifact."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ingest import metrics
from ingest.api.deps import CatalogDep, ObjectsDep, bad_request, not_found, version_or_404
from ingest.api.models import ComparisonOut, DocChangeOut, DocDiffOut
from ingest.api.views import CurrentIds, DocLookup, doc_fields, vector_columns, version_summary
from ingest.store.catalog import Catalog, VersionRow
from ingest.store.objects import ObjectStore

router = APIRouter(tags=["compare"])


def _pair(catalog: Catalog, a: int, b: int) -> tuple[VersionRow, VersionRow]:
    va, vb = version_or_404(catalog, a), version_or_404(catalog, b)
    if (va.dag, va.node) != (vb.dag, vb.node):
        raise bad_request(f"{va.label} and {vb.label} belong to different nodes")
    return va, vb


@router.get("/compare")
def compare(a: int, b: int, catalog: CatalogDep, objects: ObjectsDep) -> ComparisonOut:
    va, vb = _pair(catalog, a, b)
    result = metrics.compare_versions(catalog, objects, a, b)
    lookup = DocLookup(catalog, [vb.snapshot_id, va.snapshot_id])
    current = CurrentIds(catalog)
    changed = []
    for c in result.changed:
        fields = doc_fields(lookup.get(c.doc_id))
        changed.append(
            DocChangeOut(
                doc_id=c.doc_id,
                filename=fields["filename"],
                job_number=fields["job_number"],
                project=fields["project"],
                a_records=c.a_records,
                b_records=c.b_records,
                change=c.change,
            )
        )
    return ComparisonOut(
        a=version_summary(va, current),
        b=version_summary(vb, current),
        a_summary=dict(result.a),
        b_summary=dict(result.b),
        changed=changed,
        unchanged=result.unchanged,
    )


def _without_vectors(catalog: Catalog, objects: ObjectStore, version_id: int, doc_id: str, record: Any) -> Any:
    if not isinstance(record, list):
        return record
    import pyarrow.parquet as pq

    d = catalog.version_doc(version_id, doc_id)
    omit = set(vector_columns(pq.read_schema(objects.path(d.object_sha, "parquet"))))
    return [{k: v for k, v in row.items() if k not in omit} for row in record]


@router.get("/compare/docs/{doc_id}")
def compare_doc(a: int, b: int, doc_id: str, catalog: CatalogDep, objects: ObjectsDep) -> DocDiffOut:
    _pair(catalog, a, b)
    if catalog.version_doc(a, doc_id) is None and catalog.version_doc(b, doc_id) is None:
        raise not_found(f"Doc {doc_id!r} is in neither version")
    diff = metrics.doc_diff(catalog, objects, a, b, doc_id)
    return DocDiffOut(
        a=_without_vectors(catalog, objects, a, doc_id, diff["a"]),
        b=_without_vectors(catalog, objects, b, doc_id, diff["b"]),
        only_a=diff.get("only_a"),
        only_b=diff.get("only_b"),
    )
