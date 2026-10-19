from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

from ingest.store.objects import ObjectStore


@pytest.fixture
def store(tmp_path: Path) -> ObjectStore:
    return ObjectStore(tmp_path / "objects")


def files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def embed_table(n: int = 3, dim: int = 4) -> pa.Table:
    vectors = np.random.default_rng(0).random((n, dim), dtype=np.float32)
    region = pa.struct(
        [("page", pa.int64()), ("x0", pa.float64()), ("y0", pa.float64()), ("x1", pa.float64()), ("y1", pa.float64())]
    )
    return pa.table(
        {
            "chunk_id": pa.array([f"c{i}" for i in range(n)]),
            "seq": pa.array(range(n), pa.int64()),
            "text": pa.array(["über", "", "fee $1,750.00"][:n]),
            "pages": pa.array([[1], [1, 2], []][:n], pa.list_(pa.int64())),
            "section": pa.array([["A", "B"], [], ["C"]][:n], pa.list_(pa.string())),
            "regions": pa.array(
                [[{"page": 1, "x0": 0.0, "y0": 1.0, "x1": 2.0, "y1": 3.0}], [], [None]][:n], pa.list_(region)
            ),
            "vector": pa.FixedSizeListArray.from_arrays(pa.array(vectors.ravel(), pa.float32()), dim),
        }
    )


def test_put_json_layout_and_roundtrip(store: ObjectStore) -> None:
    obj = {"b": [1, 2.5, None], "a": {"ü": "ñ"}, "t": True}
    sha = store.put_json(obj)
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert sha == hashlib.sha256(canonical).hexdigest()
    path = store.path(sha, "json")
    assert path == store.root / sha[:2] / f"{sha}.json"
    assert path.read_bytes() == canonical
    assert store.get_json(sha) == obj
    assert store.get(sha, "json") == obj
    assert store.exists(sha, "json")
    assert not store.exists(sha, "parquet")
    assert store.size(sha, "json") == len(canonical)


def test_json_key_order_does_not_matter(store: ObjectStore) -> None:
    assert store.put_json({"a": 1, "b": 2}) == store.put_json({"b": 2, "a": 1})
    assert len(files(store.root)) == 1


def test_identical_write_is_noop(store: ObjectStore) -> None:
    sha = store.put_json({"x": 1})
    path = store.path(sha, "json")
    mtime = path.stat().st_mtime_ns
    assert store.put_json({"x": 1}) == sha
    assert path.stat().st_mtime_ns == mtime
    assert files(store.root) == [path]


def test_table_roundtrip_nested_and_vectors(store: ObjectStore) -> None:
    table = embed_table()
    sha = store.put_table(table)
    path = store.path(sha, "parquet")
    assert path == store.root / sha[:2] / f"{sha}.parquet"
    assert sha == hashlib.sha256(path.read_bytes()).hexdigest()
    back = store.get_table(sha)
    assert back.schema.equals(table.schema)
    assert back.equals(table)
    assert back.schema.field("vector").type == pa.list_(pa.float32(), 4)
    assert store.get(sha, "parquet").equals(table)
    assert store.size(sha, "parquet") == path.stat().st_size


def test_table_is_deterministic(store: ObjectStore) -> None:
    sha = store.put_table(embed_table())
    assert store.put_table(embed_table()) == sha
    assert len(files(store.root)) == 1


def test_put_table_from_pylist(store: ObjectStore) -> None:
    rows = [{"a": 1, "pages": [1, 2], "r": {"page": 1, "x0": 0.5}}, {"a": 2, "pages": [], "r": None}]
    sha = store.put_table(rows)
    assert store.get_table(sha).to_pylist() == rows
    assert store.put_table(pa.Table.from_pylist(rows)) == sha


def test_put_dispatch(store: ObjectStore) -> None:
    sha, kind = store.put({"a": 1})
    assert kind == "json" and store.get(sha, kind) == {"a": 1}
    sha, kind = store.put([{"a": 1}])
    assert kind == "parquet" and store.get(sha, kind).to_pylist() == [{"a": 1}]
    table = embed_table()
    sha, kind = store.put(table)
    assert kind == "parquet" and store.get(sha, kind).equals(table)
    for bad in ("text", 3, None, (1, 2)):
        with pytest.raises(TypeError):
            store.put(bad)


def test_no_temp_files_left(store: ObjectStore) -> None:
    store.put_json({"a": 1})
    store.put_table(embed_table())
    assert not [p for p in files(store.root) if p.name.startswith(".")]


def test_missing_and_invalid(store: ObjectStore) -> None:
    sha = "0" * 64
    assert not store.exists(sha, "json")
    with pytest.raises(FileNotFoundError):
        store.get_json(sha)
    with pytest.raises(FileNotFoundError):
        store.size(sha, "parquet")
    with pytest.raises(ValueError):
        store.path("../etc", "json")
    with pytest.raises(ValueError):
        store.path(sha, "csv")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        store.get(sha, "csv")  # type: ignore[arg-type]
