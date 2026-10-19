"""Version detail, docs, issues, revalidate, promote, debug files."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ingest.api.models import (
    DebugFile,
    IssueOut,
    PromoteResult,
    RevalidateResult,
    VersionDetail,
    VersionDocDetail,
    VersionDocsPage,
)

router = APIRouter(tags=["versions"])


@router.get("/versions/{version_id}")
def get_version(version_id: int) -> VersionDetail:
    raise NotImplementedError


@router.get("/versions/{version_id}/docs")
def version_docs(
    version_id: int,
    status: Literal["ok", "error", "skipped"] | None = None,
    q: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> VersionDocsPage:
    """Docs in snapshot (primary path) order; `q` matches filename, path or job number
    (case-insensitive substring)."""
    raise NotImplementedError


@router.get("/versions/{version_id}/docs/{doc_id}")
def version_doc(version_id: int, doc_id: str) -> VersionDocDetail:
    raise NotImplementedError


@router.get("/versions/{version_id}/issues")
def version_issues(
    version_id: int,
    level: Literal["error", "warning"] | None = None,
    source: Literal["hard", "quality", "script"] | None = None,
    doc: str | None = None,
) -> list[IssueOut]:
    raise NotImplementedError


@router.post("/versions/{version_id}/revalidate")
def revalidate(version_id: int) -> RevalidateResult:
    raise NotImplementedError


@router.post("/versions/{version_id}/promote")
def promote(version_id: int) -> PromoteResult:
    """409 with the reasons when the version isn't promotable."""
    raise NotImplementedError


@router.get("/versions/{version_id}/docs/{doc_id}/debug")
def debug_files(version_id: int, doc_id: str) -> list[DebugFile]:
    raise NotImplementedError


@router.get("/versions/{version_id}/docs/{doc_id}/debug/{name}", response_class=FileResponse)
def debug_file(version_id: int, doc_id: str, name: str) -> FileResponse:
    raise NotImplementedError
