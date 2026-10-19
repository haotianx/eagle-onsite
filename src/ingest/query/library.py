"""The library in memory (Query-Design §11): publish.library rows and documents for the query
config's ref, scoped search with duplicate collapsing, whole-document reads and document lists.

Search is exact cosine: a dot product of the L2-normalized query vector with every row in scope.
Queries are embedded by a QueryEmbedder built from the version's metadata (model, revision,
query_instruction), loaded once and reused while the version's model is unchanged."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from ingest.config import Config
from ingest.query import QueryConfig

if TYPE_CHECKING:
    import numpy as np

LIST_LIMIT = 50


class QueryEmbedder(Protocol):
    def embed(self, text: str) -> np.ndarray:
        """The query's vector: float32, shape (dim,), L2-normalized, embedded with the version's
        query instruction."""
        ...


EmbedderFactory = Callable[[Mapping[str, Any]], QueryEmbedder]
"""Builds a QueryEmbedder from version metadata (publish.version_metadata keys incl. model,
revision, dim, query_instruction)."""


@dataclass(frozen=True)
class Passage:
    """One chunk row as returned to the agent and stored in search steps and citations."""

    chunk_id: str
    doc_id: str
    seq: int
    text: str
    pages: tuple[int, ...]
    regions: tuple[Mapping[str, Any], ...]
    """{page, x0, y0, x1, y1}: PDF points, top-left origin."""
    section: tuple[str, ...]
    filename: str | None
    job_number: str | None
    project: str | None
    score: float | None = None
    """Cosine similarity for search results; None for read_document."""
    also_in: int = 0
    """Number of other library docs with a chunk of the same text_hash (collapsed in search)."""

    def to_dict(self) -> dict[str, Any]:
        """JSON-able, all fields (tuples as lists)."""
        raise NotImplementedError


@dataclass(frozen=True)
class DocumentInfo:
    doc_id: str
    paths: tuple[str, ...]
    filename: str | None
    job_number: str | None
    project: str | None
    subfolder: str | None
    pages: int | None
    upload: bool
    """From the uploads folder (removable from the chat)."""
    pending: bool
    """Processed by a single-doc run, not yet in the current version."""

    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError


class LibraryCache:
    """Thread-safe holder of the current library. Reads use an immutable snapshot, so a reload
    (seconds for ~41K rows) never blocks searches; callers on the event loop run load() and
    search() via asyncio.to_thread."""

    def __init__(self, config: Config, qconfig: QueryConfig, *, embedder_factory: EmbedderFactory | None = None) -> None:
        """No I/O. `embedder_factory` defaults to the ingest.search query embedder."""
        raise NotImplementedError

    def load(self) -> None:
        """(Re)load publish.library(catalog, objects, qconfig.ref) and swap it in. Loads are
        serialized. Raises LookupError when the ref has no current version (the previous
        library, if any, is dropped and `ready` becomes False)."""
        raise NotImplementedError

    def reload_if_stale(self) -> bool:
        """Reload when the ref's current version id differs from the loaded one (a promotion,
        or a first version appearing); returns whether it reloaded. Raises LookupError like
        load()."""
        raise NotImplementedError

    @property
    def ready(self) -> bool:
        """A library is loaded."""
        raise NotImplementedError

    @property
    def version_id(self) -> int | None:
        """Id of the loaded version."""
        raise NotImplementedError

    def warm(self) -> None:
        """Build the query embedder for the loaded version (loads the model). No-op when not
        ready."""
        raise NotImplementedError

    def hide(self, doc_id: str) -> None:
        """Exclude a doc from search, read, list and document() immediately (a removed
        upload), without waiting for a reload. Persists across reloads until unhide()."""
        raise NotImplementedError

    def unhide(self, doc_id: str) -> None:
        """Undo hide() (the doc was uploaded again)."""
        raise NotImplementedError

    def contains(self, doc_id: str) -> bool:
        """The doc is in the loaded library and not hidden."""
        raise NotImplementedError

    def document(self, doc_id: str) -> DocumentInfo | None:
        raise NotImplementedError

    def search(self, query: str, *, doc_id: str | None = None, k: int = 8) -> list[Passage]:
        """Top k passages by cosine over the rows of `doc_id` (all docs when None). Rows sharing
        a text_hash collapse to the best-scoring one, whose also_in counts the other library
        docs with that hash. Raises LookupError when not ready."""
        raise NotImplementedError

    def read(self, doc_id: str) -> list[Passage]:
        """Every row of the doc ordered by seq ([] when the doc isn't in the library). Raises
        LookupError when not ready."""
        raise NotImplementedError

    def list_documents(
        self,
        *,
        job_number: str | None = None,
        project: str | None = None,
        filename_contains: str | None = None,
        limit: int = LIST_LIMIT,
    ) -> list[DocumentInfo]:
        """Documents matching every given filter: job number exact, project and filename
        case-insensitive substring. Sorted by job number (missing last), then filename; at most
        `limit`. Raises LookupError when not ready."""
        raise NotImplementedError

    def passage(self, chunk_id: str) -> Passage | None:
        """A row by chunk id, or None."""
        raise NotImplementedError

    def close(self) -> None:
        """Close the catalog connection."""
        raise NotImplementedError
