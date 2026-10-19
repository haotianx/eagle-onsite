"""The library in memory (Query-Design §11): publish.library rows and documents for the current
embed version, reloaded when the current version changes, a single-doc run finishes or an upload
is removed; scoped search with duplicate collapsing, whole-document reads and document lists."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from ingest import publish
from ingest.publish import Library
from ingest.query import QuerySettings
from ingest.search import QueryEmbedder, VectorIndex, get_embedder
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
        self.catalog = catalog
        self.objects = objects
        self.settings = settings
        self._loader = loader or publish.library
        self._embedder = embedder or get_embedder
        self._lock = threading.Lock()
        self._loaded: _Loaded | None = None
        self._generation = 0

    def ready(self) -> bool:
        """Whether settings.ref has a current version."""
        return self._current_id() is not None

    def invalidate(self) -> None:
        """Reload on next access (a single-doc run finished, an upload was removed)."""
        with self._lock:
            self._generation += 1

    @property
    def version_id(self) -> int | None:
        """The current version the loaded library is built on."""
        loaded = self._loaded
        return loaded.version_id if loaded else None

    def document_count(self) -> int:
        return len(self._snapshot().documents)

    def search(self, query: str, *, doc_id: str | None = None, k: int | None = None) -> list[Passage]:
        """Top k (default settings.top_k) passages in scope (one doc, or all when None) by exact
        cosine; rows sharing text_hash collapse to the best-scoring one with `duplicates` set."""
        loaded = self._snapshot()
        mask = None
        if doc_id is not None:
            rows = loaded.rows_by_doc.get(doc_id)
            if rows is None:
                return []
            mask = np.zeros(len(loaded.index), dtype=bool)
            mask[rows] = True
        vector = self._embedder(loaded.library.version).embed(query)
        hits = loaded.index.search(vector, self.settings.top_k if k is None else k, mask=mask, collapse=True)
        passages = loaded.passages([h.row for h in hits])
        return [replace(p, score=h.score, duplicates=h.duplicates) for p, h in zip(passages, hits)]

    def read(self, doc_id: str) -> list[Passage]:
        """Every chunk of the doc, ordered by seq."""
        loaded = self._snapshot()
        rows = loaded.rows_by_doc.get(doc_id)
        return [] if rows is None else loaded.passages(rows.tolist())

    def list_documents(
        self, *, job_number: str | None = None, project: str | None = None, filename_contains: str | None = None
    ) -> tuple[list[DocumentInfo], int]:
        """Up to LIST_LIMIT matching documents sorted by job number, and the total match count. Job
        number matches exactly; project and filename are case-insensitive substrings."""
        project_q = project.casefold() if project else None
        filename_q = filename_contains.casefold() if filename_contains else None
        matches = [
            d
            for d in self._snapshot().sorted_documents
            if (job_number is None or d.job_number == job_number)
            and (project_q is None or project_q in (d.project or "").casefold())
            and (filename_q is None or filename_q in (d.filename or "").casefold())
        ]
        return matches[:LIST_LIMIT], len(matches)

    def document(self, doc_id: str) -> DocumentInfo | None:
        return self._snapshot().documents.get(doc_id)

    def _current_id(self) -> int | None:
        v = self.catalog.current_version(self.settings.dag, self.settings.node)
        return v.id if v else None

    def _snapshot(self) -> _Loaded:
        """The loaded library for the current version, reloading when stale. Raises LookupError
        when settings.ref has no current version."""
        current = self._current_id()
        if current is None:
            raise LookupError(f"{self.settings.ref} has no current version")
        loaded = self._loaded
        if loaded and loaded.version_id == current and loaded.generation == self._generation:
            return loaded
        with self._lock:
            loaded = self._loaded
            if loaded and loaded.version_id == current and loaded.generation == self._generation:
                return loaded
            generation = self._generation
            library = self._loader(self.catalog, self.objects, self.settings.ref)
            loaded = _Loaded.build(library, current, generation)
            self._loaded = loaded
            return loaded


@dataclass(frozen=True)
class _Loaded:
    """An immutable loaded library and its derived indexes."""

    library: Library
    version_id: int
    generation: int
    index: VectorIndex
    rows_by_doc: dict[str, np.ndarray]
    """Row indices per doc, ordered by seq."""
    documents: dict[str, DocumentInfo]
    sorted_documents: tuple[DocumentInfo, ...]

    @classmethod
    def build(cls, library: Library, version_id: int, generation: int) -> _Loaded:
        rows = library.rows
        by_doc: dict[str, list[tuple[int, int]]] = {}
        if rows.num_rows:
            for i, (doc_id, seq) in enumerate(zip(rows.column("doc_id").to_pylist(), rows.column("seq").to_pylist())):
                by_doc.setdefault(doc_id, []).append((seq, i))
        rows_by_doc = {d: np.asarray([i for _, i in sorted(items)], dtype=np.int64) for d, items in by_doc.items()}
        documents = {
            r["doc_id"]: DocumentInfo(
                doc_id=r["doc_id"],
                filename=r.get("filename"),
                paths=tuple(r.get("paths") or ()),
                job_number=r.get("job_number"),
                project=r.get("project"),
                subfolder=r.get("subfolder"),
                pages=r.get("pages"),
                upload=bool(r.get("upload")),
                pending=bool(r.get("pending")),
            )
            for r in library.documents.to_pylist()
        }
        return cls(
            library=library,
            version_id=version_id,
            generation=generation,
            index=VectorIndex.from_table(rows),
            rows_by_doc=rows_by_doc,
            documents=documents,
            sorted_documents=tuple(sorted(documents.values(), key=_document_order)),
        )

    def passages(self, indices: list[int]) -> list[Passage]:
        if not indices:
            return []
        rows = self.library.rows
        columns = [c for c in rows.column_names if c not in ("vector", "embed_text")]
        return [_passage(r) for r in rows.select(columns).take(indices).to_pylist()]


def _passage(r: dict[str, Any]) -> Passage:
    return Passage(
        chunk_id=r["chunk_id"],
        doc_id=r["doc_id"],
        seq=r["seq"],
        text=r["text"],
        pages=tuple(r.get("pages") or ()),
        section=tuple(r.get("section") or ()),
        regions=tuple(r.get("regions") or ()),
        text_hash=r.get("text_hash"),
        filename=r.get("filename"),
        job_number=r.get("job_number"),
        project=r.get("project"),
    )


def _document_order(d: DocumentInfo) -> tuple[Any, ...]:
    """By job number, numeric-aware ("1934" < "1934-03" < "2292"); without one last; then by
    filename."""
    job = tuple(int(p) if i % 2 else p for i, p in enumerate(re.split(r"(\d+)", d.job_number))) if d.job_number else ()
    return (d.job_number is None, job, (d.filename or "").casefold(), d.doc_id)
