from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from ingest.metrics import compare_versions, doc_diff, doc_metrics, version_summary
from ingest.store.catalog import Catalog, SnapshotDocInput, VersionDocInput
from ingest.store.objects import ObjectStore

PARSE = {
    "docling": {"tables": [{}, {}], "texts": []},
    "pages": [
        {"page": 1, "width": 612.0, "height": 792.0, "text_chars": 10, "ocr": False},
        {"page": 2, "width": 612.0, "height": 792.0, "text_chars": 0, "ocr": True},
        {"page": 3, "width": 612.0, "height": 792.0, "text_chars": 0, "ocr": True},
    ],
}


def chunks(*hashes: str, tokens: int = 10) -> pa.Table:
    return pa.table({"text_hash": list(hashes), "text": list(hashes), "tokens": [tokens] * len(hashes)})


@pytest.fixture
def cat(tmp_path: Path) -> Iterator[Catalog]:
    c = Catalog(tmp_path / "catalog.sqlite")
    yield c
    c.close()


@pytest.fixture
def objects(tmp_path: Path) -> ObjectStore:
    return ObjectStore(tmp_path / "objects")


def make_version(
    cat: Catalog,
    objects: ObjectStore,
    records: dict[str, Any],
    *,
    artifact_type: str | None = "chunk",
    errors: tuple[str, ...] = (),
    stats: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> int:
    ids = sorted({*records, *errors})
    snapshot_id = cat.create_snapshot("proposals", [SnapshotDocInput(d, (f"{d}.pdf",), 1, 1) for d in ids], len(ids))
    v = cat.create_version(
        dag="default", node="chunk", node_fp="fp", snapshot_id=snapshot_id, input_versions={}, run_id=None,
        commit=None, dirty=False, scratch=False, artifact_type=artifact_type, meta=meta or {},
    )
    docs = []
    for doc_id, record in records.items():
        sha, kind = objects.put(record)
        docs.append(VersionDocInput(doc_id, "ok", sha, kind))
    docs += [VersionDocInput(d, "error", error_type="E") for d in errors]
    cat.add_version_docs(v.id, docs)
    cat.finalize_version(v.id, stats or {})
    return v.id


def test_doc_metrics() -> None:
    assert doc_metrics("parse", PARSE) == {"records": 1, "pages": 3, "ocr_pages": 2, "tables": 2}
    assert doc_metrics(None, {"a": 1}) == {"records": 1}
    m = doc_metrics("chunk", pa.table({"tokens": [10, 20, 30]}))
    assert m == {"records": 3, "chunks": 3, "tokens": 60, "tokens_mean": 20}
    assert doc_metrics("chunk", [{"tokens": 4}, {"tokens": 6}])["tokens_mean"] == 5
    assert doc_metrics("chunk", pa.table({"tokens": pa.array([], pa.int64())}))["tokens_mean"] == 0
    assert doc_metrics("embed", pa.table({"x": [1, 2]})) == {"records": 2, "vectors": 2}


def test_version_summary_computed(cat: Catalog, objects: ObjectStore) -> None:
    v = make_version(cat, objects, {"d1": chunks("a", "b", tokens=10), "d2": chunks("c", tokens=40)}, errors=("d3",))
    s = version_summary(cat, objects, v)
    size = sum(objects.size(d.object_sha, "parquet") for d in cat.version_docs(v) if d.status == "ok")
    assert s == {
        "docs": 3, "ok": 2, "failed": 1, "skipped": 0, "records": 3, "size_bytes": size,
        "chunks": 3, "tokens_per_chunk": 20.0,
    }


def test_version_summary_parse_and_embed(cat: Catalog, objects: ObjectStore) -> None:
    v = make_version(cat, objects, {"d1": PARSE, "d2": PARSE}, artifact_type="parse")
    s = version_summary(cat, objects, v)
    assert (s["records"], s["pages"], s["ocr_pages"], s["tables"]) == (2, 6, 4, 4)
    v = make_version(cat, objects, {"d1": chunks("a", "b")}, artifact_type="embed", meta={"describe": {"model": "m"}})
    s = version_summary(cat, objects, v)
    assert (s["vectors"], s["model"]) == (2, "m")


def test_version_summary_from_stats(cat: Catalog, objects: ObjectStore) -> None:
    stats = {
        "docs": 10, "ok": 7, "error": 2, "skipped": 1, "cached": 3, "processed": 4, "size_bytes": 999, "wall_s": 1.5,
        "totals": {"records": 50, "chunks": 50, "tokens": 500, "tokens_mean": 70},
    }
    v = make_version(cat, objects, {"d1": chunks("a")}, stats=stats)
    assert version_summary(cat, objects, v) == {
        "docs": 10, "ok": 7, "failed": 2, "skipped": 1, "size_bytes": 999, "records": 50,
        "chunks": 50, "tokens_per_chunk": 10.0,
    }


def test_compare_versions(cat: Catalog, objects: ObjectStore) -> None:
    a = make_version(
        cat, objects,
        {"same": chunks("x", "y"), "small": chunks("a", "b", "c"), "big": chunks("p", "q"), "gone": chunks("g")},
        errors=("new",),
    )
    b = make_version(
        cat, objects,
        {"same": chunks("x", "y"), "small": chunks("a", "b", "d"), "big": chunks("r", "s", "t"),
         "new": chunks("n", "m")},
        errors=("gone",),
    )
    c = compare_versions(cat, objects, a, b)
    assert [(d.doc_id, d.a_records, d.b_records, d.change) for d in c.changed] == [
        ("big", 2, 3, 5.0),
        ("new", None, 2, 2.0),
        ("small", 3, 3, 2.0),
        ("gone", 1, None, 1.0),
    ]
    assert c.unchanged == 1
    assert c.a["docs"] == 5 and c.b["records"] == 10


def test_compare_versions_json_and_no_text_hash(cat: Catalog, objects: ObjectStore) -> None:
    a = make_version(cat, objects, {"j": {"k": 1}, "j2": {"k": 1}, "t": pa.table({"v": [1, 2]})}, artifact_type=None)
    b = make_version(cat, objects, {"j": {"k": 2}, "j2": {"k": 1}, "t": pa.table({"v": [2, 3, 4]})}, artifact_type=None)
    c = compare_versions(cat, objects, a, b)
    assert [(d.doc_id, d.change) for d in c.changed] == [("t", 3.0), ("j", 1.0)]
    assert c.unchanged == 1


def test_doc_diff(cat: Catalog, objects: ObjectStore) -> None:
    a = make_version(cat, objects, {"d": chunks("a", "b", "b"), "only": chunks("z")})
    b = make_version(cat, objects, {"d": chunks("b", "c")}, errors=("only",))
    diff = doc_diff(cat, objects, a, b, "d")
    assert diff["a"] == chunks("a", "b", "b").to_pylist()
    assert diff["b"] == chunks("b", "c").to_pylist()
    assert (diff["only_a"], diff["only_b"]) == (["a"], ["c"])
    diff = doc_diff(cat, objects, a, b, "only")
    assert diff["b"] is None and diff["only_a"] == ["z"] and diff["only_b"] == []
    assert doc_diff(cat, objects, a, b, "missing") == {"a": None, "b": None}


def test_doc_diff_json(cat: Catalog, objects: ObjectStore) -> None:
    a = make_version(cat, objects, {"d": PARSE}, artifact_type="parse")
    b = make_version(cat, objects, {}, artifact_type="parse", errors=("d",))
    assert doc_diff(cat, objects, a, b, "d") == {"a": PARSE, "b": None}
