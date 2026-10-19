from __future__ import annotations

import json

import numpy as np
import pyarrow as pa
import pytest

from ingest.publish import SCHEMA_META_KEY
from ingest.search import IndexCache, VectorIndex, table_meta


def _unit(*xs: float) -> list[float]:
    v = np.asarray(xs, dtype=np.float32)
    return (v / np.linalg.norm(v)).tolist()


def _table() -> pa.Table:
    rows = [
        ("d1", "h1", _unit(1, 0, 0)),
        ("d1", "h2", _unit(0.9, 0.1, 0)),
        ("d2", "h1", _unit(1, 0.01, 0)),
        ("d3", "h1", _unit(0.8, 0.2, 0)),
        ("d3", None, _unit(0, 1, 0)),
    ]
    t = pa.table(
        {
            "doc_id": [r[0] for r in rows],
            "text_hash": [r[1] for r in rows],
            "vector": pa.array([r[2] for r in rows], pa.list_(pa.float32(), 3)),
        }
    )
    return t.replace_schema_metadata({SCHEMA_META_KEY: json.dumps({"model": "m", "dim": 3}).encode()})


def test_exact_top_k_with_ties_by_row() -> None:
    index = VectorIndex(np.array([[1, 0], [0, 1], [1, 0]], dtype=np.float32))
    hits = index.search(np.array([1, 0], dtype=np.float32), 2)
    assert [(h.row, round(h.score, 6)) for h in hits] == [(0, 1.0), (2, 1.0)]
    assert len(index.search(np.array([1, 0], dtype=np.float32), 10)) == 3


def test_from_table_and_mask() -> None:
    t = _table()
    index = VectorIndex.from_table(t)
    assert (len(index), index.dim) == (5, 3)
    assert table_meta(t)["model"] == "m"
    mask = np.array([d == "d3" for d in t.column("doc_id").to_pylist()])
    hits = index.search(np.array(_unit(0, 1, 0), dtype=np.float32), 5, mask=mask)
    assert [h.row for h in hits] == [4, 3]


def test_collapse_keeps_best_and_counts_other_docs() -> None:
    index = VectorIndex.from_table(_table())
    hits = index.search(np.array(_unit(1, 0, 0), dtype=np.float32), 3, collapse=True)
    assert [(h.row, h.duplicates) for h in hits] == [(0, 2), (1, 0), (4, 0)]


def test_collapse_counts_only_rows_in_scope() -> None:
    index = VectorIndex.from_table(_table())
    mask = np.array([True, True, True, False, False])
    hits = index.search(np.array(_unit(1, 0, 0), dtype=np.float32), 1, mask=mask, collapse=True)
    assert [(h.row, h.duplicates) for h in hits] == [(0, 1)]


def test_empty_cases() -> None:
    empty = pa.table({"vector": pa.array([], pa.list_(pa.float32(), 3))})
    assert VectorIndex.from_table(empty).search(np.zeros(3, np.float32), 5) == []
    index = VectorIndex.from_table(_table())
    assert index.search(np.ones(3, np.float32), 5, mask=np.zeros(5, bool)) == []
    assert index.search(np.ones(3, np.float32), 0) == []


def test_index_cache_evicts_least_recent(monkeypatch: pytest.MonkeyPatch) -> None:
    loads: list[int] = []

    def fake_version_table(catalog, objects, version_id):  # noqa: ANN001
        loads.append(version_id)
        return _table()

    monkeypatch.setattr("ingest.search.version_table", fake_version_table)
    cache = IndexCache(None, None, size=2)  # type: ignore[arg-type]
    cache.get(1), cache.get(2), cache.get(1), cache.get(3), cache.get(1), cache.get(2)
    assert loads == [1, 2, 3, 2]


@pytest.mark.models
def test_sentence_transformer_embedder_matches_version_dim() -> None:
    from ingest.search import get_embedder

    meta = {
        "model": "Qwen/Qwen3-Embedding-0.6B",
        "revision": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
        "query_instruction": "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:",
    }
    embedder = get_embedder(meta)
    v = embedder.embed("aerial mapping fee")
    assert v.shape == (1024,) and v.dtype == np.float32
    assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-4
    assert get_embedder(meta) is embedder
