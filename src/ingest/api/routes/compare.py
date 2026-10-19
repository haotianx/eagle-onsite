"""Compare two versions of an artifact."""

from __future__ import annotations

from fastapi import APIRouter

from ingest.api.models import ComparisonOut, DocDiffOut

router = APIRouter(tags=["compare"])


@router.get("/compare")
def compare(a: int, b: int) -> ComparisonOut:
    raise NotImplementedError


@router.get("/compare/docs/{doc_id}")
def compare_doc(a: int, b: int, doc_id: str) -> DocDiffOut:
    raise NotImplementedError
