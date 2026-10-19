"""Side-by-side search over one or two embed versions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ingest import search as engine
from ingest.api.deps import CatalogDep, IndexCacheDep, bad_request, version_or_404
from ingest.api.models import Region, SearchHit, SearchIn, SearchOut, SearchSide
from ingest.api.views import CurrentIds, version_summary

router = APIRouter(tags=["search"])


def _hit(rank: int, score: float, row: dict[str, Any]) -> SearchHit:
    return SearchHit(
        rank=rank,
        score=score,
        chunk_id=row.get("chunk_id") or "",
        doc_id=row.get("doc_id") or "",
        text_hash=row.get("text_hash"),
        filename=row.get("filename"),
        job_number=row.get("job_number"),
        project=row.get("project"),
        pages=row.get("pages") or [],
        section=row.get("section") or [],
        text=row.get("text") or "",
        regions=[Region(**r) for r in row.get("regions") or []],
    )


@router.post("/search")
def search(body: SearchIn, catalog: CatalogDep, cache: IndexCacheDep) -> SearchOut:
    """400 for versions without a vector table."""
    current = CurrentIds(catalog)
    queries: dict[tuple[Any, ...], Any] = {}
    sides = []
    for version_id in body.versions:
        v = version_or_404(catalog, version_id)
        try:
            table, index = cache.get(version_id)
        except ValueError as e:
            raise bad_request(str(e)) from None
        if "vector" not in table.column_names:
            raise bad_request(f"{v.label} has no vectors")
        meta = engine.table_meta(table)
        key = (meta.get("model"), meta.get("revision"), meta.get("query_instruction"))
        if key not in queries:
            try:
                queries[key] = engine.get_embedder(meta).embed(body.query)
            except ValueError as e:
                raise bad_request(f"{v.label}: {e}") from None
        hits = index.search(queries[key], body.k)
        rows = table.drop_columns(["vector"]).take([h.row for h in hits]).to_pylist() if hits else []
        sides.append(
            SearchSide(
                version=version_summary(v, current),
                model=meta.get("model"),
                hits=[_hit(rank, h.score, row) for rank, (h, row) in enumerate(zip(hits, rows, strict=True), 1)],
            )
        )
    return SearchOut(sides=sides)
