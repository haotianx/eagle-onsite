from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from ingest.contract import Issue
from ingest.store.catalog import Catalog, PendingDocInput, SnapshotDocInput, VersionDocInput, VersionRow


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "catalog.sqlite"


@pytest.fixture
def cat(path: Path) -> Iterator[Catalog]:
    c = Catalog(path)
    yield c
    c.close()


def doc(doc_id: str, *paths: str, **kw: Any) -> SnapshotDocInput:
    return SnapshotDocInput(doc_id=doc_id, paths=paths, size=10, mtime_ns=123, **kw)


def snapshot(cat: Catalog, source: str = "proposals") -> int:
    return cat.create_snapshot(source, [doc("d1", "b.pdf"), doc("d2", "a.pdf")], 2)


def version(cat: Catalog, snapshot_id: int, node: str = "ocr", **kw: Any) -> VersionRow:
    args: dict[str, Any] = {
        "dag": "default",
        "node": node,
        "node_fp": "fp-" + node,
        "snapshot_id": snapshot_id,
        "input_versions": {},
        "run_id": None,
        "commit": "abc",
        "dirty": False,
        "scratch": False,
        "artifact_type": "parse",
        "meta": {"params": {"k": 1}},
    }
    args.update(kw)
    return cat.create_version(**args)


def complete(cat: Catalog, snapshot_id: int, node: str = "ocr", **kw: Any) -> VersionRow:
    return cat.finalize_version(version(cat, snapshot_id, node, **kw).id, {"docs": 2})


def is_utc_iso(ts: str) -> bool:
    return datetime.fromisoformat(ts).utcoffset().total_seconds() == 0


# Open


def test_open_creates_dirs_wal_and_foreign_keys(cat: Catalog, path: Path) -> None:
    assert path.exists()
    assert cat._one("PRAGMA journal_mode")[0] == "wal"
    assert cat._one("PRAGMA foreign_keys")[0] == 1
    assert cat._one("PRAGMA busy_timeout")[0] > 0
    tables = {r[0] for r in cat._all("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {
        "sources",
        "snapshots",
        "snapshot_docs",
        "file_index",
        "cache",
        "runs",
        "run_events",
        "run_metrics",
        "versions",
        "version_docs",
        "version_issues",
        "current",
    } <= tables


def test_reopen_keeps_data(path: Path) -> None:
    c = Catalog(path)
    sid = snapshot(c)
    c.close()
    c = Catalog(path)
    assert c.get_snapshot(sid).doc_count == 2
    c.close()


def test_foreign_keys_enforced(cat: Catalog) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        version(cat, 999)


# Transactions


def test_transaction_commits_together(cat: Catalog, path: Path) -> None:
    other = Catalog(path)
    with cat.transaction():
        run_id = cat.create_run("default", {}, scratch=False, commit=None, dirty=False)
        cat.add_event(run_id, "planned", {})
        assert other.list_runs() == []
    assert [r.id for r in other.list_runs()] == [run_id]
    assert len(other.events(run_id)) == 1
    other.close()


def test_transaction_rolls_back_on_exception(cat: Catalog) -> None:
    sid = snapshot(cat)
    with pytest.raises(RuntimeError), cat.transaction():
        run_id = cat.create_run("default", {}, scratch=False, commit=None, dirty=False)
        v = version(cat, sid, run_id=run_id)
        cat.add_version_docs(v.id, [VersionDocInput("d1", "ok", "a" * 64, "json")])
        cat.cache_put("fp", "d1", "a" * 64, "json", 1.0)
        raise RuntimeError("boom")
    assert cat.list_runs() == []
    assert cat.cache_get("fp", "d1") is None
    assert cat._one("SELECT COUNT(*) FROM versions")[0] == 0
    assert cat._one("SELECT COUNT(*) FROM version_docs")[0] == 0


def test_failed_method_inside_transaction_rolls_back_all(cat: Catalog) -> None:
    with pytest.raises(KeyError), cat.transaction():
        cat.create_run("default", {}, scratch=False, commit=None, dirty=False)
        cat.set_run_status(999, "running")
    assert cat.list_runs() == []


def test_nested_transaction(cat: Catalog) -> None:
    with cat.transaction():
        outer = cat.create_run("default", {}, scratch=False, commit=None, dirty=False)
        with pytest.raises(RuntimeError), cat.transaction():
            cat.create_run("default", {}, scratch=False, commit=None, dirty=False)
            raise RuntimeError
        with cat.transaction():
            inner = cat.create_run("default", {}, scratch=False, commit=None, dirty=False)
    assert [r.id for r in cat.list_runs()] == [inner, outer]


def test_readers_see_committed_state_while_writer_holds_transaction(cat: Catalog, path: Path) -> None:
    first = cat.create_run("default", {}, scratch=False, commit=None, dirty=False)
    readers = [Catalog(path) for _ in range(4)]
    started = threading.Event()
    release = threading.Event()
    results: list[list[int]] = []
    errors: list[Exception] = []

    def writer() -> None:
        with cat.transaction():
            cat.create_run("default", {}, scratch=False, commit=None, dirty=False)
            started.set()
            release.wait(5)

    def reader(r: Catalog) -> None:
        try:
            results.append([run.id for run in r.list_runs()])
        except Exception as e:
            errors.append(e)

    w = threading.Thread(target=writer)
    w.start()
    assert started.wait(5)
    threads = [threading.Thread(target=reader, args=(r,)) for r in readers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert not errors
    assert results == [[first]] * 4
    release.set()
    w.join(5)
    assert len(readers[0].list_runs()) == 2
    for r in readers:
        r.close()


def test_second_writer_waits_for_transaction(cat: Catalog, path: Path) -> None:
    other = Catalog(path)
    release = threading.Event()
    started = threading.Event()

    def writer() -> None:
        with cat.transaction():
            cat.create_run("default", {}, scratch=False, commit=None, dirty=False)
            started.set()
            release.wait(5)

    w = threading.Thread(target=writer)
    w.start()
    assert started.wait(5)
    threading.Timer(0.2, release.set).start()
    other.create_run("default", {}, scratch=False, commit=None, dirty=False)
    w.join(5)
    assert len(other.list_runs()) == 2
    other.close()


def test_shared_catalog_across_threads(cat: Catalog) -> None:
    run_id = cat.create_run("default", {}, scratch=False, commit=None, dirty=False)
    errors: list[Exception] = []

    def work(i: int) -> None:
        try:
            for j in range(20):
                cat.add_event(run_id, "e", {"i": i, "j": j})
                cat.events(run_id)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not errors
    assert len(cat.events(run_id)) == 80


# Snapshots


def test_snapshots(cat: Catalog) -> None:
    assert cat.latest_snapshot("proposals") is None
    docs = [
        doc("d1", "z/b.pdf", "a/copy.pdf", meta={"job_number": "2292", "project": None}),
        doc("d2", "m/a.pdf", flags=("corrupt",), flag_reason="bad xref"),
    ]
    s1 = cat.create_snapshot("proposals", docs, 3)
    s2 = cat.create_snapshot("proposals", docs[:1], 2)
    s3 = cat.create_snapshot("other", [], 0)
    row = cat.get_snapshot(s1)
    assert (row.source, row.doc_count, row.file_count) == ("proposals", 2, 3)
    assert is_utc_iso(row.created_at)
    assert cat.latest_snapshot("proposals").id == s2
    assert [s.id for s in cat.list_snapshots()] == [s3, s2, s1]
    assert [s.id for s in cat.list_snapshots("proposals")] == [s2, s1]
    rows = cat.snapshot_docs(s1)
    assert [r.doc_id for r in rows] == ["d2", "d1"]
    d1 = cat.snapshot_doc(s1, "d1")
    assert d1.paths == ("z/b.pdf", "a/copy.pdf")
    assert d1.meta == {"job_number": "2292", "project": None}
    assert d1.flags == () and d1.flag_reason is None
    d2 = cat.snapshot_doc(s1, "d2")
    assert d2.flags == ("corrupt",) and d2.flag_reason == "bad xref"
    assert (d2.size, d2.mtime_ns, d2.snapshot_id) == (10, 123, s1)
    assert cat.snapshot_doc(s2, "d2") is None
    with pytest.raises(KeyError):
        cat.get_snapshot(999)


# File index


def test_file_index_replace(cat: Catalog) -> None:
    assert cat.file_index_get("proposals") == {}
    cat.file_index_put("proposals", {"a.pdf": (1, 2, "s1"), "b.pdf": (3, 4, "s2")})
    cat.file_index_put("other", {"x.pdf": (5, 6, "s3")})
    assert cat.file_index_get("proposals") == {"a.pdf": (1, 2, "s1"), "b.pdf": (3, 4, "s2")}
    cat.file_index_put("proposals", {"b.pdf": (3, 5, "s4"), "c.pdf": (7, 8, "s5")})
    assert cat.file_index_get("proposals") == {"b.pdf": (3, 5, "s4"), "c.pdf": (7, 8, "s5")}
    assert cat.file_index_get("other") == {"x.pdf": (5, 6, "s3")}
    cat.file_index_put("proposals", {})
    assert cat.file_index_get("proposals") == {}


# Cache


def test_cache_put_get_replace(cat: Catalog) -> None:
    assert cat.cache_get("fp", "d1") is None
    cat.cache_put("fp", "d1", "sha1", "json", 1.5)
    e = cat.cache_get("fp", "d1")
    assert (e.node_fp, e.doc_id, e.object_sha, e.kind, e.duration_s) == ("fp", "d1", "sha1", "json", 1.5)
    assert is_utc_iso(e.created_at)
    cat.cache_put("fp", "d1", "sha2", "parquet", None)
    e = cat.cache_get("fp", "d1")
    assert (e.object_sha, e.kind, e.duration_s) == ("sha2", "parquet", None)
    assert cat._one("SELECT COUNT(*) FROM cache")[0] == 1
    assert cat.cache_get("other", "d1") is None


def test_cache_get_many(cat: Catalog) -> None:
    ids = [f"d{i}" for i in range(1200)]
    with cat.transaction():
        for i in ids[::2]:
            cat.cache_put("fp", i, "sha-" + i, "json", None)
        cat.cache_put("fp2", "d1", "x", "json", None)
    got = cat.cache_get_many("fp", ids + ["d0"])
    assert set(got) == set(ids[::2])
    assert got["d4"].object_sha == "sha-d4"
    assert cat.cache_get_many("fp", []) == {}


# Runs and events


def test_runs_lifecycle(cat: Catalog) -> None:
    run_id = cat.create_run("default", {"nodes": ["ocr"], "force": False}, scratch=True, commit="abc", dirty=True)
    r = cat.get_run(run_id)
    assert r.status == "queued" and r.request == {"nodes": ["ocr"], "force": False}
    assert (r.scratch, r.commit, r.dirty, r.cancel_requested) == (True, "abc", True, False)
    assert r.started_at is None and r.ended_at is None and is_utc_iso(r.created_at)
    cat.set_run_status(run_id, "running")
    r = cat.get_run(run_id)
    assert r.status == "running" and is_utc_iso(r.started_at) and r.ended_at is None
    cat.request_cancel(run_id)
    assert cat.get_run(run_id).cancel_requested
    cat.set_run_status(run_id, "failed", "boom")
    r2 = cat.get_run(run_id)
    assert r2.status == "failed" and r2.error == "boom" and r2.started_at == r.started_at
    assert is_utc_iso(r2.ended_at)
    with pytest.raises(KeyError):
        cat.get_run(999)
    with pytest.raises(KeyError):
        cat.set_run_status(999, "running")
    with pytest.raises(KeyError):
        cat.request_cancel(999)


def test_list_and_active_runs(cat: Catalog) -> None:
    a = cat.create_run("default", {}, scratch=False, commit=None, dirty=False)
    b = cat.create_run("other", {}, scratch=False, commit=None, dirty=False)
    c = cat.create_run("default", {}, scratch=False, commit=None, dirty=False)
    cat.set_run_status(a, "running")
    cat.set_run_status(c, "succeeded")
    assert [r.id for r in cat.list_runs()] == [c, b, a]
    assert [r.id for r in cat.list_runs("default")] == [c, a]
    assert [r.id for r in cat.list_runs(limit=1)] == [c]
    assert {r.id for r in cat.active_runs()} == {a, b}
    assert [r.id for r in cat.active_runs("default")] == [a]
    for status in ("succeeded", "cancelled"):
        cat.set_run_status(a, status)
        assert cat.get_run(a).status == status
    assert [r.id for r in cat.active_runs()] == [b]


def test_run_kind(cat: Catalog) -> None:
    a = cat.create_run("default", {}, scratch=False, commit=None, dirty=False)
    b = cat.create_run("default", {"doc_id": "d1"}, scratch=False, commit="abc", dirty=False, kind="single_doc")
    assert cat.get_run(a).kind == "dag"
    assert cat.get_run(b).kind == "single_doc"
    assert [r.id for r in cat.list_runs()] == [b, a]
    assert [r.id for r in cat.active_runs()] == [a]
    assert [r.id for r in cat.active_runs("default")] == [a]
    assert [r.id for r in cat.active_runs(kind="single_doc")] == [b]
    assert [r.id for r in cat.active_runs("default", kind=None)] == [a, b]
    assert cat.active_runs("other", kind=None) == []
    with pytest.raises(sqlite3.IntegrityError):
        cat.create_run("default", {}, scratch=False, commit=None, dirty=False, kind="bogus")  # type: ignore[arg-type]


def test_events_after_id(cat: Catalog) -> None:
    run_id = cat.create_run("default", {}, scratch=False, commit=None, dirty=False)
    other = cat.create_run("default", {}, scratch=False, commit=None, dirty=False)
    e1 = cat.add_event(run_id, "planned", {"nodes": ["ocr"]})
    cat.add_event(other, "planned", {})
    e2 = cat.add_event(run_id, "doc_finished", {"doc": "d1", "s": 1.5})
    evs = cat.events(run_id)
    assert [e.id for e in evs] == [e1, e2]
    assert evs[0].kind == "planned" and evs[0].payload == {"nodes": ["ocr"]} and evs[0].run_id == run_id
    assert is_utc_iso(evs[0].ts)
    assert [e.id for e in cat.events(run_id, after_id=e1)] == [e2]
    assert cat.events(run_id, after_id=e2) == []


def test_metrics(cat: Catalog) -> None:
    run_id = cat.create_run("default", {}, scratch=False, commit=None, dirty=False)
    cat.add_metrics(
        run_id, [(None, None, "wall_s", 10), ("ocr", "d1", "duration_s", 1.25), ("ocr", None, "hit_rate", 0.5)]
    )
    rows = cat.run_metrics(run_id)
    assert [(m.node, m.doc_id, m.name, m.value) for m in rows] == [
        (None, None, "wall_s", 10.0),
        ("ocr", "d1", "duration_s", 1.25),
        ("ocr", None, "hit_rate", 0.5),
    ]
    assert all(m.run_id == run_id for m in rows)
    assert cat.run_metrics(999) == []


# Versions


def test_create_version(cat: Catalog) -> None:
    sid = snapshot(cat)
    run_id = cat.create_run("default", {}, scratch=False, commit=None, dirty=False)
    v = version(cat, sid, "chunk", input_versions={"ocr": 7}, run_id=run_id, meta={"describe": {"dim": 4}})
    assert v.status == "building" and v.seq is None and v.finalized_at is None
    assert v.label == "default/chunk@building"
    assert v.input_versions == {"ocr": 7} and v.meta == {"describe": {"dim": 4}} and v.stats == {}
    assert (v.snapshot_id, v.run_id, v.commit, v.dirty, v.scratch, v.artifact_type) == (
        sid,
        run_id,
        "abc",
        False,
        False,
        "parse",
    )
    assert is_utc_iso(v.created_at)
    assert cat.get_version(v.id) == v
    with pytest.raises(KeyError):
        cat.get_version(999)


def test_seq_assigned_on_finalize(cat: Catalog) -> None:
    sid = snapshot(cat)
    a = version(cat, sid)
    b = version(cat, sid)
    abandoned = version(cat, sid)
    other = version(cat, sid, "chunk")
    cat.abandon_version(abandoned.id)
    vb = cat.finalize_version(b.id, {"docs": 2, "failed": 0})
    va = cat.finalize_version(a.id, {})
    vo = cat.finalize_version(other.id, {})
    assert (vb.seq, va.seq, vo.seq) == (1, 2, 1)
    assert vb.status == "complete" and vb.stats == {"docs": 2, "failed": 0} and is_utc_iso(vb.finalized_at)
    assert vb.label == "default/ocr@v1"
    assert cat.get_version(abandoned.id).status == "abandoned"
    assert cat.get_version(abandoned.id).seq is None
    assert cat.finalize_version(version(cat, sid).id, {}).seq == 3
    with pytest.raises(ValueError):
        cat.finalize_version(a.id, {})
    with pytest.raises(ValueError):
        cat.finalize_version(abandoned.id, {})
    with pytest.raises(ValueError):
        cat.abandon_version(a.id)
    assert cat.find_version("default", "ocr", 2).id == a.id
    assert cat.find_version("default", "ocr", 9) is None
    assert cat.find_version("default", "chunk", 1).id == other.id


def test_seq_continues_after_delete(cat: Catalog) -> None:
    sid = snapshot(cat)
    v1 = complete(cat, sid)
    v2 = complete(cat, sid)
    cat.delete_version(v2.id)
    assert complete(cat, sid).seq == 2
    cat.delete_version(v1.id)
    assert complete(cat, sid).seq == 3


def test_list_versions(cat: Catalog) -> None:
    sid = snapshot(cat)
    v1 = complete(cat, sid)
    v2 = complete(cat, sid, scratch=True)
    version(cat, sid)
    cat.abandon_version(version(cat, sid).id)
    v3 = complete(cat, sid)
    complete(cat, sid, "chunk")
    complete(cat, sid, dag="other")
    assert [v.id for v in cat.list_versions("default", "ocr")] == [v3.id, v2.id, v1.id]
    assert [v.id for v in cat.list_versions("default", "ocr", include_scratch=False)] == [v3.id, v1.id]
    assert cat.list_versions("default", "missing") == []


def test_run_versions(cat: Catalog) -> None:
    sid = snapshot(cat)
    run_id = cat.create_run("default", {}, scratch=False, commit=None, dirty=False)
    a = complete(cat, sid, "ocr", run_id=run_id)
    b = version(cat, sid, "chunk", run_id=run_id)
    complete(cat, sid, "ocr")
    assert [v.id for v in cat.run_versions(run_id)] == [a.id, b.id]


def test_version_docs(cat: Catalog) -> None:
    sid = snapshot(cat)
    v = version(cat, sid)
    cat.add_version_docs(
        v.id,
        [
            VersionDocInput(
                "d2", "ok", "a" * 64, "json", cached=True, duration_s=0.5, peak_rss_mb=100.0, worker_pid=42
            ),
            VersionDocInput(
                "d1", "error", error_type="ValueError", error_message="bad", traceback="Traceback...", duration_s=1.0
            ),
        ],
    )
    cat.add_version_docs(v.id, [VersionDocInput("d3", "skipped")])
    docs = cat.version_docs(v.id)
    assert [d.doc_id for d in docs] == ["d1", "d2", "d3"]
    d2 = cat.version_doc(v.id, "d2")
    assert d2.version_id == v.id and d2.status == "ok" and d2.cached is True
    assert (d2.object_sha, d2.kind, d2.duration_s, d2.peak_rss_mb, d2.worker_pid) == ("a" * 64, "json", 0.5, 100.0, 42)
    d1 = cat.version_doc(v.id, "d1")
    assert (d1.status, d1.error_type, d1.error_message, d1.traceback) == ("error", "ValueError", "bad", "Traceback...")
    assert d1.cached is False and d1.object_sha is None
    assert cat.version_doc(v.id, "nope") is None
    with pytest.raises(sqlite3.IntegrityError):
        cat.add_version_docs(v.id, [VersionDocInput("d1", "ok")])


def test_delete_version(cat: Catalog) -> None:
    sid = snapshot(cat)
    v = complete(cat, sid)
    cat.add_version_docs(v.id, [VersionDocInput("d1", "ok", "a" * 64, "json")])
    cat.add_issues(v.id, "hard", [("d1", Issue("error", "x", "y"))])
    cat.set_current("default", "ocr", v.id)
    with pytest.raises(ValueError):
        cat.delete_version(v.id)
    other = complete(cat, sid)
    cat.set_current("default", "ocr", other.id)
    cat.delete_version(v.id)
    with pytest.raises(KeyError):
        cat.get_version(v.id)
    assert cat.version_docs(v.id) == [] and cat.issues(v.id) == []
    with pytest.raises(KeyError):
        cat.delete_version(v.id)


# Current


def test_current_and_promotion(cat: Catalog) -> None:
    sid = snapshot(cat)
    assert cat.current_version("default", "ocr") is None
    assert cat.current_versions("default") == {}
    v1 = complete(cat, sid)
    v2 = complete(cat, sid)
    c1 = complete(cat, sid, "chunk")
    cat.set_current("default", "ocr", v1.id)
    cat.set_current("default", "chunk", c1.id)
    assert cat.current_version("default", "ocr") == cat.get_version(v1.id)
    cat.set_current("default", "ocr", v2.id)
    assert cat.current_version("default", "ocr").id == v2.id
    cat.set_current("default", "ocr", v1.id)
    assert {k: v.id for k, v in cat.current_versions("default").items()} == {"ocr": v1.id, "chunk": c1.id}
    assert cat.current_versions("other") == {}
    with pytest.raises(ValueError):
        cat.set_current("default", "chunk", v1.id)
    with pytest.raises(ValueError):
        cat.set_current("default", "ocr", version(cat, sid).id)
    with pytest.raises(KeyError):
        cat.set_current("default", "ocr", 999)


# Pending and removed docs


def test_pending_docs(cat: Catalog) -> None:
    sid = snapshot(cat)
    v1 = complete(cat, sid, "embed")
    v2 = complete(cat, sid, "embed")
    run = cat.create_run("default", {}, scratch=False, commit=None, dirty=False, kind="single_doc")
    assert cat.pending_docs() == []
    cat.add_pending_doc(PendingDocInput("u1", v1.id, run, ("uploads/u1.pdf",), {"filename": "u1.pdf"}, "a" * 64, 3))
    cat.add_pending_doc(PendingDocInput("u2", v1.id, run, ("uploads/u2.pdf",), {}, "b" * 64))
    cat.add_pending_doc(PendingDocInput("u1", v2.id, run, ("uploads/u1.pdf",), {}, "c" * 64, 3))
    rows = cat.pending_docs(v1.id)
    assert [(r.doc_id, r.object_sha[0], r.pages) for r in rows] == [("u1", "a", 3), ("u2", "b", None)]
    assert rows[0].paths == ("uploads/u1.pdf",) and rows[0].meta == {"filename": "u1.pdf"}
    assert (rows[0].version_id, rows[0].run_id) == (v1.id, run) and is_utc_iso(rows[0].created_at)
    assert [(r.doc_id, r.version_id) for r in cat.pending_docs()] == [("u1", v1.id), ("u2", v1.id), ("u1", v2.id)]
    # Replacing a (doc_id, version_id) row updates it and moves it to the end.
    cat.add_pending_doc(PendingDocInput("u1", v1.id, run, ("uploads/u1.pdf",), {}, "d" * 64, 4))
    assert [(r.doc_id, r.object_sha[0], r.pages) for r in cat.pending_docs(v1.id)] == [("u2", "b", None), ("u1", "d", 4)]
    assert len(cat.pending_docs()) == 3
    assert cat.pending_docs(999) == []
    with pytest.raises(sqlite3.IntegrityError):
        cat.add_pending_doc(PendingDocInput("u3", 999, run, (), {}, "e" * 64))
    cat.delete_version(v2.id)
    assert [r.version_id for r in cat.pending_docs()] == [v1.id, v1.id]


def test_removed_docs(cat: Catalog) -> None:
    assert cat.removed_docs() == set()
    cat.add_removed_doc("u1")
    cat.add_removed_doc("u2")
    cat.add_removed_doc("u1")
    assert cat.removed_docs() == {"u1", "u2"}


# Issues


def test_issues(cat: Catalog) -> None:
    sid = snapshot(cat)
    v = complete(cat, sid)
    cat.add_issues(
        v.id, "hard", [("d1", Issue("error", "dim", "wrong dim")), (None, Issue("error", "coverage", "missing"))]
    )
    cat.add_issues(v.id, "quality", [("d1", Issue("warning", "no_text", "empty page", page=3))])
    rows = cat.issues(v.id)
    assert [(r.doc_id, r.source, r.level, r.code, r.page) for r in rows] == [
        ("d1", "hard", "error", "dim", None),
        (None, "hard", "error", "coverage", None),
        ("d1", "quality", "warning", "no_text", 3),
    ]
    assert rows[0].message == "wrong dim" and rows[0].version_id == v.id and rows[0].checks_fp is None
    assert [r.code for r in cat.issues(v.id, "d1")] == ["dim", "no_text"]
    assert cat.issues(v.id, "d2") == []


def test_replace_issues_by_source(cat: Catalog) -> None:
    sid = snapshot(cat)
    v = complete(cat, sid)
    other = complete(cat, sid)
    cat.add_issues(v.id, "hard", [("d1", Issue("error", "h", "hard"))])
    cat.add_issues(v.id, "quality", [("d1", Issue("warning", "q", "quality"))])
    cat.add_issues(v.id, "script", [("d1", Issue("warning", "s1", "old"))], checks_fp="c1")
    cat.add_issues(other.id, "script", [("d1", Issue("warning", "s0", "other"))], checks_fp="c1")
    cat.replace_issues(v.id, "script", [("d2", Issue("error", "s2", "new", page=1))], checks_fp="c2")
    rows = cat.issues(v.id)
    assert [(r.source, r.code, r.checks_fp) for r in rows] == [
        ("hard", "h", None),
        ("quality", "q", None),
        ("script", "s2", "c2"),
    ]
    assert [r.code for r in cat.issues(other.id)] == ["s0"]
    cat.replace_issues(v.id, "script", [])
    assert [r.source for r in cat.issues(v.id)] == ["hard", "quality"]


def test_doc_metrics_round_trip(tmp_path):
    from ingest.store.catalog import Catalog, SnapshotDocInput, VersionDocInput

    cat = Catalog(tmp_path / "c.sqlite")
    cat.cache_put("fp", "d1", "a" * 64, "parquet", 1.5, {"chunks": 3.0, "tokens": 90.0})
    assert cat.cache_get("fp", "d1").metrics == {"chunks": 3.0, "tokens": 90.0}
    cat.cache_put("fp", "d2", "b" * 64, "json", None)
    assert cat.cache_get("fp", "d2").metrics == {}
    snap = cat.create_snapshot("s", [SnapshotDocInput("d1", ("a.pdf",), 1, 1)], 1)
    v = cat.create_version(
        dag="g", node="n", node_fp="fp", snapshot_id=snap, input_versions={}, run_id=None,
        commit=None, dirty=False, scratch=False, artifact_type="chunk", meta={},
    )
    cat.add_version_docs(v.id, [VersionDocInput("d1", "ok", "a" * 64, "parquet", metrics={"chunks": 3.0})])
    assert cat.version_doc(v.id, "d1").metrics == {"chunks": 3.0}
    cat.close()
