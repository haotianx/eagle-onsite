from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import ingest
from ingest.config import load_config
from ingest.contract import text_hash
from ingest.publish import SCHEMA_META_KEY, load_table, resolve_ref, version_metadata, version_table, write_published
from ingest.store.catalog import Catalog, SnapshotDocInput, VersionDocInput
from ingest.store.objects import ObjectStore

DIM = 4
DESCRIBE = {"model": "m", "revision": "r1", "dim": DIM, "metric": "cosine", "query_instruction": "q"}
SNAPSHOT = [
    SnapshotDocInput("d1", ("2292 SZEKERES/CONTRACT/b.pdf",), 10, 1, {"job_number": "2292", "filename": "b.pdf"}),
    SnapshotDocInput("d2", ("1000 ACME/a.pdf", "copy/a.pdf"), 10, 1, {"job_number": "1000", "project": "ACME"}),
    SnapshotDocInput("d3", ("3000 ERR/c.pdf",), 10, 1, {}),
]


def embed_table(doc_id: str, texts: list[str]) -> pa.Table:
    vectors = np.ones((len(texts), DIM), dtype=np.float32) / 2
    return pa.table(
        {
            "chunk_id": [f"{doc_id}-{i}" for i in range(len(texts))],
            "doc_id": [doc_id] * len(texts),
            "seq": pa.array(range(len(texts)), pa.int64()),
            "text": texts,
            "text_hash": [text_hash(t) for t in texts],
            "tokens": pa.array([len(t) for t in texts], pa.int64()),
            "vector": pa.FixedSizeListArray.from_arrays(pa.array(vectors.ravel()), DIM),
        }
    )


def build(cat: Catalog, objects: ObjectStore, node: str = "embed", *, current: bool = True) -> int:
    snapshot_id = cat.create_snapshot("proposals", SNAPSHOT, 4)
    v = cat.create_version(
        dag="default",
        node=node,
        node_fp="fp",
        snapshot_id=snapshot_id,
        input_versions={},
        run_id=None,
        commit="abc",
        dirty=False,
        scratch=False,
        artifact_type="embed",
        meta={"describe": DESCRIBE},
    )
    docs = [
        VersionDocInput("d1", "ok", objects.put_table(embed_table("d1", ["Shared  boilerplate", "x"])), "parquet"),
        VersionDocInput("d2", "ok", objects.put_table(embed_table("d2", ["shared boilerplate"])), "parquet"),
        VersionDocInput("d3", "error", error_type="ValueError", error_message="bad"),
    ]
    cat.add_version_docs(v.id, docs)
    cat.finalize_version(v.id, {})
    if current:
        cat.set_current("default", node, v.id)
    return v.id


@pytest.fixture
def cat(tmp_path: Path) -> Iterator[Catalog]:
    c = Catalog(tmp_path / "catalog.sqlite")
    yield c
    c.close()


@pytest.fixture
def objects(tmp_path: Path) -> ObjectStore:
    return ObjectStore(tmp_path / "objects")


def test_resolve_ref(cat: Catalog, objects: ObjectStore) -> None:
    v1 = build(cat, objects)
    v2 = build(cat, objects, current=False)
    assert resolve_ref(cat, "default/embed").id == v1
    assert resolve_ref(cat, "default/embed@v2").id == v2
    assert resolve_ref(cat, "default/embed@v1").id == v1
    for ref in ("default/embed@v3", "default/chunk", "other/embed", "default", "default/embed@2", "a/b/c"):
        with pytest.raises(LookupError):
            resolve_ref(cat, ref)


def test_version_metadata(cat: Catalog, objects: ObjectStore) -> None:
    meta = version_metadata(cat, build(cat, objects))
    assert meta["version"] == "default/embed@v1"
    assert (meta["dag"], meta["node"], meta["seq"]) == ("default", "embed", 1)
    assert (meta["commit"], meta["scratch"]) == ("abc", False)
    assert {k: meta[k] for k in DESCRIBE} == DESCRIBE
    assert {"snapshot_id", "node_fp", "created_at"} <= meta.keys()


def test_version_table(cat: Catalog, objects: ObjectStore) -> None:
    t = version_table(cat, objects, build(cat, objects))
    assert t.num_rows == 3
    # snapshot order is by primary path: d2 ("1000 ACME/…") before d1 ("2292 …"); error doc d3 excluded.
    assert t.column("doc_id").to_pylist() == ["d2", "d1", "d1"]
    assert t.column("paths").to_pylist() == [["1000 ACME/a.pdf", "copy/a.pdf"], *[["2292 SZEKERES/CONTRACT/b.pdf"]] * 2]
    assert t.column("job_number").to_pylist() == ["1000", "2292", "2292"]
    assert t.column("project").to_pylist() == ["ACME", None, None]
    assert t.column("filename").to_pylist() == [None, "b.pdf", "b.pdf"]
    assert t.column("subfolder").to_pylist() == [None, None, None]
    assert t.column("dup_count").to_pylist() == [2, 2, 1]
    assert t.schema.field("dup_count").type == pa.int64()
    assert t.schema.field("vector").type == pa.list_(pa.float32(), DIM)
    meta = json.loads(t.schema.metadata[SCHEMA_META_KEY])
    assert meta["version"] == "default/embed@v1" and meta["model"] == "m"


def test_version_table_empty(cat: Catalog, objects: ObjectStore) -> None:
    snapshot_id = cat.create_snapshot("proposals", SNAPSHOT, 4)
    v = cat.create_version(
        dag="default", node="embed", node_fp="fp", snapshot_id=snapshot_id, input_versions={}, run_id=None,
        commit=None, dirty=False, scratch=True, artifact_type="embed", meta={},
    )
    cat.add_version_docs(v.id, [VersionDocInput("d1", "error")])
    t = version_table(cat, objects, v.id)
    assert t.num_rows == 0
    assert t.column_names == ["paths", "job_number", "project", "subfolder", "filename"]
    assert SCHEMA_META_KEY in t.schema.metadata


def test_version_table_rejects_json(cat: Catalog, objects: ObjectStore) -> None:
    snapshot_id = cat.create_snapshot("proposals", SNAPSHOT, 4)
    v = cat.create_version(
        dag="default", node="ocr", node_fp="fp", snapshot_id=snapshot_id, input_versions={}, run_id=None,
        commit=None, dirty=False, scratch=False, artifact_type="parse", meta={},
    )
    cat.add_version_docs(v.id, [VersionDocInput("d1", "ok", objects.put_json({"pages": []}), "json")])
    with pytest.raises(ValueError):
        version_table(cat, objects, v.id)


def test_load_table(tmp_repo: Path) -> None:
    config = load_config(tmp_repo)
    cat = Catalog(config.catalog_path)
    try:
        build(cat, ObjectStore(config.objects_dir))
    finally:
        cat.close()
    t = load_table("default/embed@v1", repo_root=tmp_repo)
    assert t.column("doc_id").to_pylist() == ["d2", "d1", "d1"]
    assert ingest.load_table("default/embed", repo_root=tmp_repo).equals(t)


def test_write_published(cat: Catalog, objects: ObjectStore, tmp_path: Path) -> None:
    v = build(cat, objects)
    path = write_published(cat, objects, v, tmp_path / "published")
    assert path == tmp_path / "published" / "default" / "embed.parquet"
    assert [p.name for p in path.parent.iterdir()] == ["embed.parquet"]
    written: Any = pq.read_table(path)
    expected = version_table(cat, objects, v)
    assert written.equals(expected)
    assert written.schema.field("vector").type == pa.list_(pa.float32(), DIM)
    assert json.loads(written.schema.metadata[SCHEMA_META_KEY]) == version_metadata(cat, v)
