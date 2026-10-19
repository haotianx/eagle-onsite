"""In-memory search (Ingestion-Design §15, Query-Design §11): exact cosine (dot product on
L2-normalized vectors) over a version's vectors, with queries embedded by the version's recorded
model, revision and query instruction."""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

import numpy as np

from ingest.publish import SCHEMA_META_KEY, version_table
from ingest.store.catalog import Catalog
from ingest.store.objects import ObjectStore

if TYPE_CHECKING:
    import pyarrow as pa


def table_meta(table: pa.Table) -> dict[str, Any]:
    """The version metadata stored in a version table's schema (publish.version_metadata),
    including describe() keys: model, revision, dim, metric, query_instruction."""
    raw = (table.schema.metadata or {}).get(SCHEMA_META_KEY)
    return json.loads(raw) if raw else {}


class QueryEmbedder(Protocol):
    def embed(self, query: str) -> np.ndarray:
        """A float32 L2-normalized vector."""
        ...


class SentenceTransformerEmbedder:
    """Embeds queries with sentence-transformers on Metal (MPS) when available."""

    def __init__(self, model: str, revision: str | None = None, query_instruction: str | None = None) -> None:
        import torch
        from sentence_transformers import SentenceTransformer

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.instruction = query_instruction
        self._model = SentenceTransformer(model, revision=revision, device=device)
        self._lock = threading.Lock()

    def embed(self, query: str) -> np.ndarray:
        with self._lock:
            v = self._model.encode(
                [query],
                prompt=self.instruction,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )[0]
        return _normalize(np.asarray(v, dtype=np.float32))


_embedders: dict[tuple[str, str | None, str | None], QueryEmbedder] = {}
_embedders_lock = threading.Lock()


def get_embedder(meta: Mapping[str, Any]) -> QueryEmbedder:
    """The process-wide embedder for a version's (model, revision, query_instruction), loaded once.
    Raises ValueError when `meta` names no model."""
    model = meta.get("model")
    if not model:
        raise ValueError("version metadata has no embedding model")
    key = (str(model), meta.get("revision"), meta.get("query_instruction"))
    with _embedders_lock:
        if key not in _embedders:
            _embedders[key] = SentenceTransformerEmbedder(*key)
        return _embedders[key]


@dataclass(frozen=True)
class Hit:
    row: int
    """Row index in the indexed table."""
    score: float
    duplicates: int = 0
    """With collapse: other groups (documents) in scope whose rows share this row's key."""


class VectorIndex:
    """Exact search over an (n, dim) float32 matrix of normalized vectors. `keys` (e.g. text_hash)
    and `groups` (e.g. doc_id) per row enable duplicate collapsing."""

    def __init__(
        self,
        vectors: np.ndarray,
        *,
        keys: Sequence[str | None] | None = None,
        groups: Sequence[str] | None = None,
    ) -> None:
        self.vectors = np.ascontiguousarray(vectors, dtype=np.float32)
        if self.vectors.ndim != 2:
            raise ValueError("vectors must be a 2-D array")
        n = len(self.vectors)
        if (keys is not None and len(keys) != n) or (groups is not None and len(groups) != n):
            raise ValueError("keys and groups must have one entry per vector")
        self.keys = list(keys) if keys is not None else None
        self.groups = np.asarray(groups, dtype=object) if groups is not None else None
        self._rows_by_key: dict[str, np.ndarray] | None = None

    @classmethod
    def from_table(
        cls, table: pa.Table, *, column: str = "vector", key: str | None = "text_hash", group: str | None = "doc_id"
    ) -> VectorIndex:
        """Index a version table (missing key/group columns disable collapsing)."""
        dim = int(table_meta(table).get("dim") or 0)
        if column in table.column_names and table.num_rows:
            arr = table.column(column).combine_chunks()
            flat = arr.flatten().to_numpy(zero_copy_only=False).astype(np.float32, copy=False)
            vectors = flat.reshape(table.num_rows, -1)
        else:
            vectors = np.zeros((table.num_rows, dim), dtype=np.float32)
        keys = table.column(key).to_pylist() if key and key in table.column_names else None
        groups = table.column(group).to_pylist() if group and group in table.column_names else None
        return cls(vectors, keys=keys, groups=groups)

    def __len__(self) -> int:
        return len(self.vectors)

    @property
    def dim(self) -> int:
        return self.vectors.shape[1]

    def scores(self, query: np.ndarray) -> np.ndarray:
        return self.vectors @ np.asarray(query, dtype=np.float32)

    def search(
        self, query: np.ndarray, k: int, *, mask: np.ndarray | None = None, collapse: bool = False
    ) -> list[Hit]:
        """Top `k` rows by score among rows where `mask` is True (all when None), best first,
        ties by row. With `collapse`, rows sharing a key keep only the best-scoring one, whose
        `duplicates` counts the other groups in scope with that key."""
        if k <= 0 or len(self) == 0:
            return []
        scores = self.scores(query)
        candidates = np.arange(len(self)) if mask is None else np.flatnonzero(mask)
        if not len(candidates):
            return []
        cand_scores = scores[candidates]
        if not collapse or self.keys is None:
            take = min(k, len(candidates))
            top = np.argpartition(-cand_scores, take - 1)[:take] if take < len(candidates) else np.arange(len(candidates))
            order = sorted(top, key=lambda i: (-cand_scores[i], candidates[i]))
            return [Hit(int(candidates[i]), float(cand_scores[i])) for i in order]

        order = np.lexsort((candidates, -cand_scores))
        hits: list[Hit] = []
        seen: set[str] = set()
        for i in order:
            row = int(candidates[i])
            key = self.keys[row]
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)
            hits.append(Hit(row, float(cand_scores[i]), self._duplicates(row, mask)))
            if len(hits) == k:
                break
        return hits

    def _duplicates(self, row: int, mask: np.ndarray | None) -> int:
        key = self.keys[row] if self.keys is not None else None
        if key is None:
            return 0
        if self._rows_by_key is None:
            by_key: dict[str, list[int]] = {}
            for i, k in enumerate(self.keys):
                if k is not None:
                    by_key.setdefault(k, []).append(i)
            self._rows_by_key = {k: np.asarray(v) for k, v in by_key.items()}
        rows = self._rows_by_key[key]
        if mask is not None:
            rows = rows[mask[rows]]
        if self.groups is None:
            return len(rows) - 1
        own = self.groups[row]
        return len({g for g in self.groups[rows] if g != own})


class IndexCache:
    """Version tables and their indexes, loaded on demand and kept for the `size` most recently
    used versions. Thread-safe."""

    def __init__(self, catalog: Catalog, objects: ObjectStore, size: int = 4) -> None:
        self.catalog = catalog
        self.objects = objects
        self.size = size
        self._items: OrderedDict[int, tuple[pa.Table, VectorIndex]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, version_id: int) -> tuple[pa.Table, VectorIndex]:
        """Raises KeyError for an unknown version, ValueError for a non-table version."""
        with self._lock:
            if version_id in self._items:
                self._items.move_to_end(version_id)
                return self._items[version_id]
            table = version_table(self.catalog, self.objects, version_id)
            item = (table, VectorIndex.from_table(table))
            self._items[version_id] = item
            while len(self._items) > self.size:
                self._items.popitem(last=False)
            return item


def _normalize(v: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(v))
    return v / norm if norm > 0 else v
