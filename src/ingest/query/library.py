"""The library in memory (Query-Design §11): publish.library rows and documents for the query
config's ref, scoped search with duplicate collapsing, whole-document reads and document lists.

Search is exact cosine: a dot product of the L2-normalized query vector with every row in scope.
Queries are embedded by a QueryEmbedder built from the version's metadata (model, revision,
query_instruction), loaded once and reused while the version's model is unchanged."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from ingest import publish
from ingest.config import Config
from ingest.query import QueryConfig
from ingest.search import VectorIndex
from ingest.store.catalog import Catalog, VersionRow
from ingest.store.objects import ObjectStore

if TYPE_CHECKING:
    import numpy as np
    import pyarrow as pa

LIST_LIMIT = 50


class QueryEmbedder(Protocol):
    def embed(self, text: str) -> np.ndarray:
        """The query's vector: float32, shape (dim,), L2-normalized, embedded with the version's
        query instruction."""
        ...


EmbedderFactory = Callable[[Mapping[str, Any]], QueryEmbedder]
"""Builds a QueryEmbedder from version metadata (publish.version_metadata keys incl. model,
revision, dim, query_instruction)."""

_EMBEDDER_KEYS = ("model", "revision", "query_instruction")
_REGION_COORDS = ("x0", "y0", "x1", "y1")
_PASSAGE_COLUMNS = (
    "chunk_id", "doc_id", "seq", "text", "pages", "section", "regions", "filename", "job_number", "project"
)
_DIGITS = re.compile(r"[0-9]+")


def default_embedder_factory(meta: Mapping[str, Any]) -> QueryEmbedder:
    from ingest import search

    return search.get_embedder(meta)


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
    """Search only: number of other docs in scope whose chunks with the same text_hash were
    collapsed into this one."""

    def to_dict(self) -> dict[str, Any]:
        """JSON-able, all fields (tuples as lists)."""
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "seq": self.seq,
            "text": self.text,
            "pages": list(self.pages),
            "regions": [_region(r) for r in self.regions],
            "section": list(self.section),
            "filename": self.filename,
            "job_number": self.job_number,
            "project": self.project,
            "score": self.score,
            "also_in": self.also_in,
        }


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
        return {
            "doc_id": self.doc_id,
            "paths": list(self.paths),
            "filename": self.filename,
            "job_number": self.job_number,
            "project": self.project,
            "subfolder": self.subfolder,
            "pages": self.pages,
            "upload": self.upload,
            "pending": self.pending,
        }


@dataclass(frozen=True)
class _Snapshot:
    version_id: int
    version: Mapping[str, Any]
    index: VectorIndex
    """Every row's vector, text_hash (collapse key) and doc_id (group)."""
    row_doc: np.ndarray
    """int32 (rows,): index into doc_ids."""
    doc_ids: tuple[str, ...]
    doc_index: Mapping[str, int]
    doc_rows: Mapping[str, np.ndarray]
    """doc_id → row indices ordered by seq."""
    chunk_rows: Mapping[str, int]
    documents: Mapping[str, DocumentInfo]
    columns: Mapping[str, list[Any]]


class LibraryCache:
    """Thread-safe holder of the current library. Reads use an immutable snapshot, so a reload
    (seconds for ~41K rows) never blocks searches; callers on the event loop run load() and
    search() via asyncio.to_thread."""

    def __init__(self, config: Config, qconfig: QueryConfig, *, embedder_factory: EmbedderFactory | None = None) -> None:
        """No I/O. `embedder_factory` defaults to the ingest.search query embedder."""
        self._config = config
        self._qconfig = qconfig
        self._embedder_factory = embedder_factory or default_embedder_factory
        self._catalog: Catalog | None = None
        self._objects: ObjectStore | None = None
        self._snapshot: _Snapshot | None = None
        self._hidden: frozenset[str] = frozenset()
        self._lock = threading.Lock()
        self._load_lock = threading.RLock()
        self._embed_lock = threading.Lock()
        self._embedder: QueryEmbedder | None = None
        self._embedder_key: tuple[Any, ...] | None = None

    def load(self) -> None:
        """(Re)load publish.library(catalog, objects, qconfig.ref) and swap it in. Loads are
        serialized. Raises LookupError when the ref has no current version (the previous
        library, if any, is dropped and `ready` becomes False)."""
        with self._load_lock:
            catalog, objects = self._open()
            lib: publish.Library | None = None
            version_id = 0
            for _ in range(2):
                v = self._current_version()
                try:
                    lib = publish.library(catalog, objects, self._qconfig.ref)
                except LookupError:
                    self._drop()
                    raise
                version_id = v.id
                if lib.version.get("seq") == v.seq:
                    break
            assert lib is not None
            snapshot = _build_snapshot(lib, version_id)
            with self._lock:
                self._snapshot = snapshot

    def reload_if_stale(self) -> bool:
        """Reload when the ref's current version id differs from the loaded one (a promotion,
        or a first version appearing); returns whether it reloaded. Raises LookupError like
        load()."""
        with self._load_lock:
            if self._current_version().id == self.version_id:
                return False
            self.load()
            return True

    @property
    def ready(self) -> bool:
        """A library is loaded."""
        return self._snapshot is not None

    @property
    def version_id(self) -> int | None:
        """Id of the loaded version."""
        snap = self._snapshot
        return None if snap is None else snap.version_id

    def warm(self) -> None:
        """Build the query embedder for the loaded version (loads the model). No-op when not
        ready."""
        snap = self._snapshot
        if snap is not None:
            with self._embed_lock:
                self._get_embedder(snap.version)

    def hide(self, doc_id: str) -> None:
        """Exclude a doc from search, read, list and document() immediately (a removed
        upload), without waiting for a reload. Persists across reloads until unhide()."""
        with self._lock:
            self._hidden = self._hidden | {doc_id}

    def unhide(self, doc_id: str) -> None:
        """Undo hide() (the doc was uploaded again)."""
        with self._lock:
            self._hidden = self._hidden - {doc_id}

    def contains(self, doc_id: str) -> bool:
        """The doc is in the loaded library and not hidden."""
        return self.document(doc_id) is not None

    def document(self, doc_id: str) -> DocumentInfo | None:
        snap, hidden = self._state()
        if snap is None or doc_id in hidden:
            return None
        return snap.documents.get(doc_id)

    def search(self, query: str, *, doc_id: str | None = None, k: int = 8) -> list[Passage]:
        """Top k passages by cosine over the rows of `doc_id` (all docs when None), via
        ingest.search.VectorIndex. Rows sharing a text_hash collapse to the best-scoring one,
        whose also_in counts the other docs in scope with that hash. Raises LookupError when not
        ready."""
        import numpy as np

        snap, hidden = self._ready_state()
        mask: np.ndarray | None = None
        if doc_id is not None:
            if doc_id in hidden or doc_id not in snap.doc_rows:
                return []
            mask = np.zeros(len(snap.index), dtype=bool)
            mask[snap.doc_rows[doc_id]] = True
        else:
            hidden_codes = [snap.doc_index[d] for d in hidden if d in snap.doc_index]
            if hidden_codes:
                mask = ~np.isin(snap.row_doc, hidden_codes)
        if k <= 0 or len(snap.index) == 0:
            return []
        with self._embed_lock:
            vector = np.asarray(self._get_embedder(snap.version).embed(query), dtype=np.float32)
        hits = snap.index.search(vector, k, mask=mask, collapse=True)
        return [_passage(snap, hit.row, hit.score, also_in=hit.duplicates) for hit in hits]

    def read(self, doc_id: str) -> list[Passage]:
        """Every row of the doc ordered by seq ([] when the doc isn't in the library). Raises
        LookupError when not ready."""
        snap, hidden = self._ready_state()
        if doc_id in hidden or doc_id not in snap.doc_rows:
            return []
        return [_passage(snap, int(row), None) for row in snap.doc_rows[doc_id]]

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
        snap, hidden = self._ready_state()
        project_q = project.casefold() if project else None
        filename_q = filename_contains.casefold() if filename_contains else None
        matches = [
            d
            for d in snap.documents.values()
            if d.doc_id not in hidden
            and (not job_number or d.job_number == job_number)
            and (project_q is None or (d.project is not None and project_q in d.project.casefold()))
            and (filename_q is None or (d.filename is not None and filename_q in d.filename.casefold()))
        ]
        matches.sort(key=_document_sort_key)
        return matches[: max(limit, 0)]

    def passage(self, chunk_id: str) -> Passage | None:
        """A row by chunk id, or None."""
        snap, hidden = self._state()
        if snap is None:
            return None
        row = snap.chunk_rows.get(chunk_id)
        if row is None or snap.columns["doc_id"][row] in hidden:
            return None
        return _passage(snap, row, None)

    def close(self) -> None:
        """Close the catalog connection."""
        with self._load_lock:
            if self._catalog is not None:
                self._catalog.close()
                self._catalog = None

    def _open(self) -> tuple[Catalog, ObjectStore]:
        if self._catalog is None:
            self._catalog = Catalog(self._config.catalog_path)
            self._objects = ObjectStore(self._config.objects_dir)
        assert self._objects is not None
        return self._catalog, self._objects

    def _current_version(self) -> VersionRow:
        """The ref's current VersionRow; drops the loaded library and raises LookupError when
        there is none."""
        catalog, _ = self._open()
        v = catalog.current_version(self._qconfig.dag, self._qconfig.node)
        if v is None:
            self._drop()
            raise LookupError(f"{self._qconfig.ref} has no current version")
        return v

    def _drop(self) -> None:
        with self._lock:
            self._snapshot = None

    def _state(self) -> tuple[_Snapshot | None, frozenset[str]]:
        with self._lock:
            return self._snapshot, self._hidden

    def _ready_state(self) -> tuple[_Snapshot, frozenset[str]]:
        snap, hidden = self._state()
        if snap is None:
            raise LookupError("the library is not loaded")
        return snap, hidden

    def _get_embedder(self, meta: Mapping[str, Any]) -> QueryEmbedder:
        """Call with _embed_lock held."""
        key = tuple(meta.get(k) for k in _EMBEDDER_KEYS)
        if self._embedder is None or self._embedder_key != key:
            self._embedder = None
            self._embedder = self._embedder_factory(meta)
            self._embedder_key = key
        return self._embedder


def _build_snapshot(lib: publish.Library, version_id: int) -> _Snapshot:
    import numpy as np

    rows = lib.rows
    n = rows.num_rows
    columns = {
        name: _column(rows, name, n)
        for name in (*_PASSAGE_COLUMNS, "text_hash")
    }
    index = VectorIndex(_matrix(rows, n, lib.version.get("dim")), keys=columns["text_hash"], groups=columns["doc_id"])

    documents = {
        d["doc_id"]: DocumentInfo(
            doc_id=d["doc_id"],
            paths=tuple(d.get("paths") or ()),
            filename=d.get("filename"),
            job_number=d.get("job_number"),
            project=d.get("project"),
            subfolder=d.get("subfolder"),
            pages=d.get("pages"),
            upload=bool(d.get("upload")),
            pending=bool(d.get("pending")),
        )
        for d in lib.documents.to_pylist()
    }

    doc_ids = tuple(dict.fromkeys([*documents, *columns["doc_id"]]))
    doc_index = {d: i for i, d in enumerate(doc_ids)}
    row_doc = np.fromiter((doc_index[d] for d in columns["doc_id"]), dtype=np.int32, count=n)
    seq = np.asarray([-1 if s is None else s for s in columns["seq"]], dtype=np.int64)
    order = np.lexsort((seq, row_doc))
    codes = row_doc[order]
    starts = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]]) if n else np.array([], dtype=np.int64)
    groups = np.split(order, starts[1:]) if n else []
    doc_rows = {doc_ids[codes[start]]: group for start, group in zip(starts, groups, strict=True)}

    return _Snapshot(
        version_id=version_id,
        version=dict(lib.version),
        index=index,
        row_doc=row_doc,
        doc_ids=doc_ids,
        doc_index=doc_index,
        doc_rows=doc_rows,
        chunk_rows={c: i for i, c in enumerate(columns["chunk_id"])},
        documents=documents,
        columns=columns,
    )


def _column(table: pa.Table, name: str, n: int) -> list[Any]:
    return table.column(name).to_pylist() if name in table.column_names else [None] * n


def _matrix(table: pa.Table, n: int, dim: Any) -> np.ndarray:
    import numpy as np

    if "vector" not in table.column_names:
        return np.zeros((n, int(dim or 0)), dtype=np.float32)
    col = table.column("vector").combine_chunks()
    width = col.type.list_size
    values = col.flatten().to_numpy(zero_copy_only=False)
    return np.ascontiguousarray(values.reshape(n, width), dtype=np.float32)


def _passage(snap: _Snapshot, row: int, score: float | None, *, also_in: int = 0) -> Passage:
    c = snap.columns
    return Passage(
        chunk_id=c["chunk_id"][row],
        doc_id=c["doc_id"][row],
        seq=c["seq"][row],
        text=c["text"][row],
        pages=tuple(c["pages"][row] or ()),
        regions=tuple(_region(r) for r in c["regions"][row] or ()),
        section=tuple(c["section"][row] or ()),
        filename=c["filename"][row],
        job_number=c["job_number"][row],
        project=c["project"][row],
        score=score,
        also_in=also_in,
    )


def _region(r: Mapping[str, Any]) -> dict[str, Any]:
    return {"page": int(r["page"]), **{k: float(r[k]) for k in _REGION_COORDS}}


def _document_sort_key(d: DocumentInfo) -> tuple[Any, ...]:
    job = d.job_number
    if job is None:
        job_key: tuple[Any, ...] = (2, 0, "")
    elif _DIGITS.fullmatch(job):
        job_key = (0, int(job), job)
    else:
        job_key = (1, 0, job.casefold())
    return (*job_key, d.filename is None, (d.filename or "").casefold(), d.doc_id)
