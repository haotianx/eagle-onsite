"""Original PDFs."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["docs"])


@router.get("/docs/{doc_id}/pdf", response_class=FileResponse)
def doc_pdf(doc_id: str) -> FileResponse:
    """The document's original file. 410 "Document removed" for removed uploads; 404 "Original
    changed or missing" when no file with this content exists any more."""
    raise NotImplementedError
