from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from ingest.contract import text_hash
from ingest.query import QuerySettings
from ingest.query.library import LIST_LIMIT, LibraryCache

A, B, C, D = ("a" * 64, "b" * 64, "c" * 64, "d" * 64)


class FakeCatalog:
    def __init__(self, version_id: int | None = 1) -> None:
        self.version_id = version_id

    def current_version(self, dag: str, node: str) -> SimpleNamespace | None:
        assert (dag, node) == ("default", "embed")
        return SimpleNamespace(id=self.version_id) if self.version_id is not None else None


class Loader:
    def __init__(self, make, delay: float = 0.0) -> None:
        self.make = make
        self.delay = delay
        self.calls = 0

    def __call__(self, catalog, objects, ref):
        assert ref == "default/embed"
        self.calls += 1
        time.sleep(self.delay)
        return self.make(catalog)


@pytest.fixture
def setup(library, fake_embedder):
    catalog = FakeCatalog()
    loader = Loader(lambda _: library)
    cache = LibraryCache(catalog, None, QuerySettings(), loader=loader, embedder=lambda meta: fake_embedder)
    return cache, catalog, loader


def test_single_document_search_stays_in_scope(setup):
    cache, _, _ = setup
    hits = cache.search("aerial mapping fee", doc_id=B)
    assert hits and {h.doc_id for h in hits} == {B}
    assert all(h.score is not None for h in hits)
    assert cache.search("aerial mapping", doc_id="unknown") == []


def test_boilerplate_collapses_with_duplicates(setup, fake_docs):
    cache, _, _ = setup
    boilerplate = fake_docs[0].chunks[2][0]
    hits = cache.search(boilerplate, k=20)
    boiler = [h for h in hits if h.text_hash == text_hash(boilerplate)]
    assert len(boiler) == 1 and boiler[0].duplicates == 2
    assert hits[0] is boiler[0]
    scoped = cache.search(boilerplate, doc_id=A)
    assert scoped[0].text == boilerplate and scoped[0].duplicates == 0


def test_search_default_k_and_passage_fields(library, fake_embedder):
    cache = LibraryCache(
        FakeCatalog(), None, QuerySettings(top_k=2), loader=lambda *_: library, embedder=lambda meta: fake_embedder
    )
    hits = cache.search("aerial mapping fee")
    assert len(hits) == 2
    top = hits[0]
    assert (top.doc_id, top.seq, top.pages, top.section) == (A, 1, (1,), ("Fees",))
    assert top.filename == "2292 Szekeres Proposal.pdf" and top.job_number == "2292" and top.project == "SZEKERES"
    assert top.regions[0]["page"] == 1


def test_embedder_gets_version_metadata(library, fake_embedder):
    seen = []
    cache = LibraryCache(
        FakeCatalog(), None, QuerySettings(), loader=lambda *_: library, embedder=lambda m: seen.append(m) or fake_embedder
    )
    cache.search("fee")
    assert seen[0]["model"] == "fake-embedder"


def test_read_returns_chunks_in_seq_order(setup, fake_docs):
    cache, _, _ = setup
    passages = cache.read(B)
    assert [p.seq for p in passages] == [0, 1, 2]
    assert [p.text for p in passages] == [c[0] for c in fake_docs[1].chunks]
    assert all(p.score is None for p in passages)
    assert cache.read("unknown") == []


def test_list_documents_filters(setup):
    cache, _, _ = setup
    docs, total = cache.list_documents()
    assert [d.doc_id for d in docs] == [A, B, C, D] and total == 4
    assert cache.list_documents(job_number="3002")[0][0].doc_id == B
    assert cache.list_documents(job_number="300") == ([], 0)
    assert [d.doc_id for d in cache.list_documents(project="colina")[0]] == [C]
    assert [d.doc_id for d in cache.list_documents(filename_contains="PROPOSAL")[0]] == [A, B, C]
    assert cache.list_documents(project="elliot", filename_contains="addendum") == ([], 0)
    d = cache.document(D)
    assert d is not None and d.upload and d.pending and d.job_number is None and d.paths == ("uploads/Retainer Letter.pdf",)
    assert cache.document("unknown") is None
    assert cache.document_count() == 4


def test_list_documents_sort_and_limit(library_factory, fake_doc_type, fake_embedder):
    def doc(i: int, job: str | None, name: str):
        return fake_doc_type(f"{i:064d}", f"X/{name}", job, "P", None, [(f"text {i}", [1], [])])

    def cache_for(docs):
        lib = library_factory(docs)
        return LibraryCache(FakeCatalog(), None, QuerySettings(), loader=lambda *_: lib, embedder=lambda m: fake_embedder)

    docs = [
        doc(1, "2292", "b.pdf"),
        doc(2, None, "z.pdf"),
        doc(3, "1934-03", "c.pdf"),
        doc(4, None, "A.pdf"),
        doc(5, "1934", "d.pdf"),
        doc(6, "2292", "a.pdf"),
    ]
    listed, total = cache_for(docs).list_documents()
    assert [d.filename for d in listed] == ["d.pdf", "c.pdf", "a.pdf", "b.pdf", "A.pdf", "z.pdf"] and total == 6

    many = [doc(i, str(5000 + i), f"f{i}.pdf") for i in range(LIST_LIMIT + 5)]
    listed, total = cache_for(many).list_documents(project="p")
    assert total == LIST_LIMIT + 5 and len(listed) == LIST_LIMIT
    assert listed[0].filename == "f0.pdf"


def test_reload_on_invalidate_and_version_change(library_factory, fake_embedder):
    catalog = FakeCatalog(1)
    loader = Loader(lambda c: library_factory(version_id=c.version_id))
    cache = LibraryCache(catalog, None, QuerySettings(), loader=loader, embedder=lambda m: fake_embedder)
    assert cache.version_id is None
    cache.document_count()
    cache.read(A)
    assert loader.calls == 1 and cache.version_id == 1
    cache.invalidate()
    cache.read(A)
    assert loader.calls == 2
    catalog.version_id = 2
    cache.search("fee")
    assert loader.calls == 3 and cache.version_id == 2
    cache.list_documents()
    assert loader.calls == 3


def test_not_ready_without_current_version(library, fake_embedder):
    catalog = FakeCatalog(None)
    cache = LibraryCache(catalog, None, QuerySettings(), loader=lambda *_: library, embedder=lambda m: fake_embedder)
    assert not cache.ready()
    with pytest.raises(LookupError):
        cache.read(A)
    catalog.version_id = 1
    assert cache.ready()


def test_real_catalog_current_version(tmp_path, library, fake_embedder):
    from ingest.store.catalog import Catalog

    catalog = Catalog(tmp_path / "catalog.sqlite")
    try:
        cache = LibraryCache(catalog, None, QuerySettings(), loader=lambda *_: library, embedder=lambda m: fake_embedder)
        assert not cache.ready()
    finally:
        catalog.close()


def test_concurrent_reads_during_reload(library_factory, fake_embedder):
    catalog = FakeCatalog(1)
    loader = Loader(lambda c: library_factory(version_id=c.version_id), delay=0.05)
    cache = LibraryCache(catalog, None, QuerySettings(), loader=loader, embedder=lambda m: fake_embedder)
    cache.read(A)
    errors: list[Exception] = []
    stop = threading.Event()

    def reader() -> None:
        try:
            while not stop.is_set():
                assert len(cache.read(B)) == 3
                assert {h.doc_id for h in cache.search("pickle ball", doc_id=B)} == {B}
                assert cache.list_documents()[1] == 4
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=reader) for _ in range(6)]
    for t in threads:
        t.start()
    for v in (2, 3):
        time.sleep(0.02)
        catalog.version_id = v
    time.sleep(0.02)
    cache.invalidate()
    time.sleep(0.15)
    stop.set()
    for t in threads:
        t.join()
    assert not errors
    assert loader.calls >= 3 and cache.version_id == 3
