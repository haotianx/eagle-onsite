"""Side-by-side search over one or two embed versions."""

from __future__ import annotations

from fastapi import APIRouter

from ingest.api.models import SearchIn, SearchOut

router = APIRouter(tags=["search"])


@router.post("/search")
def search(body: SearchIn) -> SearchOut:
    raise NotImplementedError
