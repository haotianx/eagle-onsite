from __future__ import annotations

import json
import threading
from collections.abc import Iterator, Mapping
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

from ingest import publish
from ingest.config import Config
from ingest.contract import text_hash
from ingest.publish import Library
from ingest.query import QueryConfig
from ingest.query.library import LIST_LIMIT, LibraryCache
from ingest.store.catalog import Catalog, SnapshotDocInput, VersionDocInput
from ingest.store.objects import ObjectStore

DIM = 4
BOILERPLATE = "Terms and conditions apply."
REGION = {"page": 1, "x0": 10, "y0": 20, "x1": 300, "y1": 40}


def unit(*v: float) -> np.ndarray:
    a = np.asarray(v, dtype=np.float32)
    return a / np.linalg.norm(a)


QUERIES = {"x": unit(1, 0, 0, 0), "y": unit(0, 1, 0, 0), "z": unit(0, 0, 1, 0)}


class FakeEmbedder:
    def __init__(self, meta: Mapping[str, Any]) -> None:
        self.meta = dict(meta)

    def embed(self, text: str) -> np.ndarray:
        return QUERIES[text]


class Factory:
    def __init__(self) -> None:
        self.built: list[FakeEmbedder] = []

    def __call__(self, meta: Mapping[str, Any]) -> FakeEmbedder:
        e = FakeEmbedder(meta)
        self.built.append(e)
        return e


def chunk(doc_id: str, seq: int, text: str, vector: np.ndarray, **meta: Any) -> dict[str, Any]:
    return {
        "chunk_id": f"{doc_id}-{seq}",
        "doc_id": doc_id,
        "seq": seq,
        "text": text,
        "embed_text": text,
        "pages": [seq + 1],
        "section": ["S"],
        "regions": [{**REGION, "page": seq + 1}],
        "text_hash": text_hash(text),
        "tokens": len(text),
        "vector": vector,
        "paths": [f"{doc_id}.pdf"],
        "job_number": meta.get("job_number"),
        "project": meta.get("project"),
        "subfolder": None,
        "filename": meta.get("filename", f"{doc_id}.pdf"),
        "dup_count": 1,
    }


ROW_SCHEMA = pa.schema(
    [
        ("chunk_id", pa.string()),
        ("doc_id", pa.string()),
        ("seq", pa.int64()),
        ("text", pa.string()),
        ("embed_text", pa.string()),
        ("pages", pa.list_(pa.int64())),
        ("section", pa.list_(pa.string())),
        (
            "regions",
            pa.list_(pa.struct([("page", pa.int64()), *((k, pa.float64()) for k in ("x0", "y0", "x1", "y1"))])),
        ),
        ("text_hash", pa.string()),
        ("tokens", pa.int64()),
        ("vector", pa.list_(pa.float32(), DIM)),
        ("paths", pa.list_(pa.string())),
        ("job_number", pa.string()),
        ("project", pa.string()),
        ("subfolder", pa.string()),
        ("filename", pa.string()),
        ("dup_count", pa.int64()),
    ]
)


def make_library(rows: list[dict[str, Any]], docs: list[dict[str, Any]], seq: int, model: str = "m") -> Library:
    rows = [{**r, "vector": np.asarray(r["vector"], dtype=np.float32).tolist()} for r in rows]
    documents = [
        {
            "doc_id": d["doc_id"],
            "paths": [f"{d['doc_id']}.pdf"],
            "job_number": d.get("job_number"),
            "project": d.get("project"),
            "subfolder": None,
            "filename": d.get("filename", f"{d['doc_id']}.pdf"),
            "pages": 3,
            "upload": d.get("upload", False),
            "pending": False,
        }
        for d in docs
    ]
    version = {"seq": seq, "model": model, "revision": "r1", "dim": DIM, "query_instruction": "q"}
    return Library(pa.Table.from_pylist(rows, schema=ROW_SCHEMA), pa.Table.from_pylist(documents), version)


def default_library(seq: int = 1, model: str = "m") -> Library:
    # Rows deliberately out of seq order within a doc.
    rows = [
        chunk("a", 1, "alpha one", unit(1, 1, 0, 0), job_number="100", project="Acme Tower"),
        chunk("a", 0, "alpha zero", unit(1, 0.1, 0, 0), job_number="100", project="Acme Tower"),
        chunk("a", 2, BOILERPLATE, unit(1, 0, 1, 0), job_number="100", project="Acme Tower"),
        chunk("b", 0, "beta zero", unit(0, 1, 0, 0), job_number="20"),
        chunk("b", 1, BOILERPLATE, unit(1, 0, 0.9, 0), job_number="20"),
        chunk("c", 0, BOILERPLATE.upper(), unit(1, 0, 1.1, 0)),
    ]
    docs = [
        {"doc_id": "a", "job_number": "100", "project": "Acme Tower", "filename": "Spec.pdf"},
        {"doc_id": "b", "job_number": "20", "filename": "contract.pdf"},
        {"doc_id": "c", "filename": "notes.pdf", "upload": True},
        {"doc_id": "e", "job_number": "20", "filename": "empty.pdf"},
    ]
    return make_library(rows, docs, seq, model)


class Env:
    def __init__(self, config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
        self.catalog = Catalog(config.catalog_path)
        self.libraries: dict[int, Library] = {}
        self.calls = 0
        self.before_library = lambda: None

        def library(catalog: Catalog, objects: ObjectStore, ref: str) -> Library:
            assert ref == "default/embed"
            self.calls += 1
            self.before_library()
            v = catalog.current_version("default", "embed")
            if v is None:
                raise LookupError(ref)
            return self.libraries[v.seq]

        monkeypatch.setattr(publish, "library", library)

    def promote(self, lib: Library) -> int:
        snapshot_id = self.catalog.create_snapshot("proposals", [SnapshotDocInput("a", ("a.pdf",), 1, 1)], 1)
        v = self.catalog.create_version(
            dag="default", node="embed", node_fp="fp", snapshot_id=snapshot_id, input_versions={}, run_id=None,
            commit=None, dirty=False, scratch=False, artifact_type="embed", meta={},
        )
        self.catalog.add_version_docs(v.id, [VersionDocInput("a", "error")])
        seq = self.catalog.finalize_version(v.id, {}).seq
        assert seq is not None
        self.libraries[seq] = lib
        self.catalog.set_current("default", "embed", v.id)
        return v.id


@pytest.fixture
def env(config: Config, monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    e = Env(config, monkeypatch)
    yield e
    e.catalog.close()


@pytest.fixture
def factory() -> Factory:
    return Factory()


@pytest.fixture
def cache(config: Config, qconfig: QueryConfig, factory: Factory) -> Iterator[LibraryCache]:
    c = LibraryCache(config, qconfig, embedder_factory=factory)
    yield c
    c.close()


@pytest.fixture
def loaded(env: Env, cache: LibraryCache) -> LibraryCache:
    env.promote(default_library())
    cache.load()
    return cache


def ids(passages: list[Any]) -> list[str]:
    return [p.chunk_id for p in passages]


def test_search_orders_by_score_and_collapses_duplicates(loaded: LibraryCache) -> None:
    results = loaded.search("x", k=10)
    # The boilerplate rows (a-2, b-1, c-0: same text_hash) collapse to the best-scoring b-1.
    assert ids(results) == ["a-0", "b-1", "a-1", "b-0"]
    scores = [p.score for p in results]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == pytest.approx(float(unit(1, 0.1, 0, 0)[0]))
    assert [p.also_in for p in results] == [0, 2, 0, 0]
    assert ids(loaded.search("x", k=2)) == ["a-0", "b-1"]


def test_search_scoped_to_one_document(loaded: LibraryCache) -> None:
    results = loaded.search("y", doc_id="b", k=10)
    assert ids(results) == ["b-0", "b-1"]
    assert results[1].also_in == 2
    assert ids(loaded.search("z", doc_id="a", k=1)) == ["a-2"]
    assert loaded.search("x", doc_id="missing") == []
    assert loaded.search("x", doc_id="e") == []


def test_read_orders_by_seq(loaded: LibraryCache) -> None:
    passages = loaded.read("a")
    assert ids(passages) == ["a-0", "a-1", "a-2"]
    assert [p.score for p in passages] == [None] * 3
    assert [p.also_in for p in passages] == [0, 0, 2]
    assert loaded.read("e") == []
    assert loaded.read("missing") == []


def test_passage_and_to_dict(loaded: LibraryCache) -> None:
    p = loaded.passage("a-2")
    assert p is not None
    assert (p.doc_id, p.seq, p.pages, p.section, p.filename, p.job_number, p.project) == (
        "a", 2, (3,), ("S",), "a.pdf", "100", "Acme Tower",
    )
    d = p.to_dict()
    assert d["regions"] == [{"page": 3, "x0": 10.0, "y0": 20.0, "x1": 300.0, "y1": 40.0}]
    assert isinstance(d["regions"][0]["page"], int) and isinstance(d["regions"][0]["x0"], float)
    assert d["pages"] == [3] and d["section"] == ["S"] and d["also_in"] == 2 and d["score"] is None
    assert json.loads(json.dumps(d)) == d
    assert loaded.passage("nope") is None

    info = loaded.document("c")
    assert info is not None and info.upload and not info.pending
    di = info.to_dict()
    assert di["paths"] == ["c.pdf"] and di["pages"] == 3
    assert json.loads(json.dumps(di)) == di


def test_list_documents(loaded: LibraryCache) -> None:
    def names(docs: list[Any]) -> list[str]:
        return [d.doc_id for d in docs]

    # Numeric-aware job sort (20 before 100), then filename; missing job last.
    assert names(loaded.list_documents()) == ["b", "e", "a", "c"]
    assert names(loaded.list_documents(job_number="20")) == ["b", "e"]
    assert names(loaded.list_documents(job_number="2")) == []
    assert names(loaded.list_documents(project="acme")) == ["a"]
    assert names(loaded.list_documents(filename_contains="PDF", job_number="20")) == ["b", "e"]
    assert names(loaded.list_documents(filename_contains="spec", project="tower")) == ["a"]
    assert names(loaded.list_documents(job_number="", project="", filename_contains="")) == ["b", "e", "a", "c"]
    assert names(loaded.list_documents(limit=2)) == ["b", "e"]


def test_list_documents_limit(env: Env, cache: LibraryCache) -> None:
    docs = [{"doc_id": f"d{i:03}", "job_number": "1"} for i in range(LIST_LIMIT + 10)]
    env.promote(make_library([], docs, 1))
    cache.load()
    listed = cache.list_documents()
    assert len(listed) == LIST_LIMIT
    assert [d.doc_id for d in listed] == [f"d{i:03}" for i in range(LIST_LIMIT)]
    assert cache.search("x") == []


def test_hide_and_unhide_across_reloads(env: Env, loaded: LibraryCache) -> None:
    loaded.hide("a")
    assert not loaded.contains("a") and loaded.document("a") is None
    assert all(p.doc_id != "a" for p in loaded.search("x", k=10))
    assert loaded.search("x", doc_id="a") == [] and loaded.read("a") == []
    assert loaded.passage("a-0") is None
    assert "a" not in [d.doc_id for d in loaded.list_documents()]

    env.promote(default_library(seq=2))
    assert loaded.reload_if_stale()
    assert not loaded.contains("a")

    loaded.unhide("a")
    assert loaded.contains("a")
    assert loaded.search("x", k=1)[0].doc_id == "a"


def test_reload_if_stale(env: Env, cache: LibraryCache) -> None:
    v1 = env.promote(default_library())
    assert cache.reload_if_stale()
    assert cache.version_id == v1
    assert not cache.reload_if_stale()

    newer = make_library([chunk("n", 0, "new", unit(1, 0, 0, 0))], [{"doc_id": "n"}], 2)
    v2 = env.promote(newer)
    assert cache.reload_if_stale()
    assert cache.version_id == v2
    assert ids(cache.search("x")) == ["n-0"]
    assert not cache.contains("a")


def test_load_retries_when_promoted_during_load(env: Env, cache: LibraryCache) -> None:
    v1 = env.promote(default_library())
    v2: list[int] = []

    def promote_once() -> None:
        if not v2:
            v2.append(env.promote(default_library(seq=2)))

    env.before_library = promote_once
    cache.load()
    assert env.calls == 2
    assert cache.version_id == v2[0] != v1


def test_not_ready_without_current_version(env: Env, cache: LibraryCache) -> None:
    assert not cache.ready and cache.version_id is None
    with pytest.raises(LookupError):
        cache.load()
    with pytest.raises(LookupError):
        cache.reload_if_stale()
    assert not cache.ready
    for call in (lambda: cache.search("x"), lambda: cache.read("a"), cache.list_documents):
        with pytest.raises(LookupError):
            call()
    assert not cache.contains("a") and cache.document("a") is None and cache.passage("a-0") is None
    cache.warm()

    env.promote(default_library())
    cache.load()
    assert cache.ready
    env.catalog._conn.execute("DELETE FROM current")
    with pytest.raises(LookupError):
        cache.load()
    assert not cache.ready and cache.version_id is None


def test_embedder_reused_until_model_changes(env: Env, cache: LibraryCache, factory: Factory) -> None:
    env.promote(default_library())
    cache.load()
    assert factory.built == []
    cache.warm()
    assert len(factory.built) == 1 and factory.built[0].meta["model"] == "m"

    env.promote(default_library(seq=2))
    cache.reload_if_stale()
    cache.search("x")
    assert len(factory.built) == 1

    env.promote(default_library(seq=3, model="m2"))
    cache.reload_if_stale()
    cache.search("x")
    assert len(factory.built) == 2 and factory.built[1].meta["model"] == "m2"


def test_search_during_reload(env: Env, loaded: LibraryCache) -> None:
    started, release = threading.Event(), threading.Event()

    def block() -> None:
        started.set()
        assert release.wait(5)

    env.before_library = block
    env.promote(default_library(seq=2))
    errors: list[BaseException] = []

    def reload() -> None:
        try:
            loaded.reload_if_stale()
        except BaseException as e:
            errors.append(e)

    t = threading.Thread(target=reload)
    t.start()
    try:
        assert started.wait(5)
        assert ids(loaded.search("x", k=1)) == ["a-0"]
        assert loaded.read("b") and loaded.list_documents()
    finally:
        release.set()
        t.join(5)
    assert not errors
    assert loaded.version_id == env.catalog.current_version("default", "embed").id  # type: ignore[union-attr]
