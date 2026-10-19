"""The library in memory (Query-Design §11): publish.library rows and documents for the current
embed version, reloaded when the current version changes, a single-doc run finishes or an upload
is removed; scoped search with duplicate collapsing, whole-document reads and document lists."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ingest.publish import Library
from ingest.query import QuerySettings
from ingest.search import QueryEmbedder
from ingest.store.catalog import Catalog
from ingest.store.objects import ObjectStore

LIST_LIMIT = 50


@dataclass(frozen=True)
class Passage:
    chunk_id: str
    doc_id: str
    seq: int
    text: str
    pages: tuple[int, ...]
    section: tuple[str, ...]
    regions: tuple[dict[str, Any], ...]
    """{page, x0, y0, x1, y1}: PDF points, top-left origin."""
    text_hash: str | None
    filename: str | None
    job_number: str | None
    project: str | None
    score: float | None = None
    """search() only."""
    duplicates: int = 0
    """search() only: other documents in scope with the same passage (collapsed)."""


@dataclass(frozen=True)
class DocumentInfo:
    doc_id: str
    filename: str | None
    paths: tuple[str, ...]
    job_number: str | None
    project: str | None
    subfolder: str | None
    pages: int | None
    upload: bool
    pending: bool


class LibraryCache:
    """Thread-safe. Every read first checks the current version of settings.ref and reloads
    when it changed or after invalidate()."""

    def __init__(
        self,
        catalog: Catalog,
        objects: ObjectStore,
        settings: QuerySettings,
        *,
        loader: Callable[[Catalog, ObjectStore, str], Library] | None = None,
        embedder: Callable[[dict[str, Any]], QueryEmbedder] | None = None,
    ) -> None:
        """`loader` defaults to publish.library, `embedder` to search.get_embedder (given the
        library's version metadata)."""
        raise NotImplementedError

    def ready(self) -> bool:
        """Whether settings.ref has a current version."""
        raise NotImplementedError

    def invalidate(self) -> None:
        """Reload on next access (a single-doc run finished, an upload was removed)."""
        raise NotImplementedError

    @property
    def version_id(self) -> int | None:
        """The current version the loaded library is built on."""
        raise NotImplementedError

    def document_count(self) -> int:
        raise NotImplementedError

    def search(self, query: str, *, doc_id: str | None = None, k: int | None = None) -> list[Passage]:
        """Top k (default settings.top_k) passages in scope (one doc, or all when None) by exact
        cosine; rows sharing text_hash collapse to the best-scoring one with `duplicates` set."""
        raise NotImplementedError

    def read(self, doc_id: str) -> list[Passage]:
        """Every chunk of the doc, ordered by seq."""
        raise NotImplementedError

    def list_documents(
        self, *, job_number: str | None = None, project: str | None = None, filename_contains: str | None = None
    ) -> tuple[list[DocumentInfo], int]:
        """Up to LIST_LIMIT matching documents sorted by job number, and the total match count. Job
        number matches exactly; project and filename are case-insensitive substrings."""
        raise NotImplementedError

    def document(self, doc_id: str) -> DocumentInfo | None:
        raise NotImplementedError
