from __future__ import annotations

import json
import shutil
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest
from conftest import FIXTURES
from engine_util import NODES, SZEKERES, Env, commit_all, git, write_dag

from ingest import gitinfo
from ingest.engine.executor import run
from ingest.engine.planner import RunRequest
from ingest.engine.single_doc import SingleDocRunner, docs_to_reprocess, lineage
from ingest.publish import library
from ingest.sources import sync
from ingest.state import promote
from ingest.store.catalog import RunRow
from ingest.store.objects import ObjectStore

REPO = Path(__file__).resolve().parents[2]
AGUILAR = "3426 AGUILAR COLINA FUERTE/CONTRACT/PROPOSAL ADDENDUM.pdf"

DAG = """\
name: toy
source: proposals
nodes:
  size:
    script: nodes/size.py
    params: {salt: 1, raise_on: raise-me, exit_on: crash-me, sleep_on: hang-me, sleep_s: 60}
    timeout: 3
  rows:
    script: nodes/rows.py
    inputs: [size]
    params: {n: 2}
    checks: nodes/checks/rows.py
  join:
    script: nodes/join.py
    inputs: [rows, size]
"""


def write_config(repo: Path, primary: Path) -> None:
    (repo / "ingest.toml").write_text(
        f'data_dir = ".ingest"\n\n[sources.proposals]\nfolders = ["{primary}", "{{data_dir}}/uploads"]\n'
    )


def promote_all(e: Env, versions: dict[str, int]) -> None:
    for vid in versions.values():
        promote(e.catalog, e.config, vid)


def full_run(e: Env, dag: str = "toy") -> dict[str, int]:
    result = run(e.config, RunRequest(dag=dag))
    assert result.status == "succeeded", e.catalog.get_run(result.run_id).error
    return dict(result.versions)


def upload(e: Env, name: str, source: str = SZEKERES, salt: bool = True) -> Path:
    """Copy a fixture into the uploads folder (with a trailing comment so it's a new doc)."""
    dest = e.config.uploads_dir / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = (FIXTURES / source).read_bytes()
    dest.write_bytes(data + (f"\n%{name}\n".encode() if salt else b""))
    return dest


def finished(e: Env, run_id: int, timeout: float = 60.0) -> RunRow:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = e.catalog.get_run(run_id)
        if r.status in ("succeeded", "failed", "cancelled"):
            return r
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} still {e.catalog.get_run(run_id).status}")


def version_count(e: Env) -> int:
    return sum(len(e.catalog.list_versions("toy", n)) for n in ("size", "rows", "join"))


@pytest.fixture
def sd(tmp_repo: Path) -> Iterator[Env]:
    """Toy DAG over [fixtures, uploads], fully run and promoted."""
    write_config(tmp_repo, FIXTURES)
    shutil.copytree(NODES, tmp_repo / "nodes")
    write_dag(tmp_repo, DAG)
    commit_all(tmp_repo)
    e = Env(tmp_repo)
    promote_all(e, full_run(e))
    yield e
    e.catalog.close()


@pytest.fixture
def runner(sd: Env) -> Iterator[SingleDocRunner]:
    r = SingleDocRunner(sd.config, dag="toy", node="join")
    r.start()
    yield r
    r.close()


def test_lineage(sd: Env) -> None:
    current = sd.catalog.current_versions("toy")
    nodes = lineage(sd.catalog, "toy", "join")
    assert [n.node for n in nodes] == ["size", "rows", "join"]
    head = gitinfo.head_commit(sd.repo)
    for n in nodes:
        v = current[n.node]
        assert (n.version_id, n.node_fp, n.commit) == (v.id, v.node_fp, head)
        assert (n.script, n.script_hash) == (v.meta["script"], v.meta["script_hash"])
    size, rows, join = nodes
    assert size.params["salt"] == 1 and size.timeout == 3.0 and size.checks is None and size.inputs == ()
    assert rows.params == {"n": 2} and rows.checks == "nodes/checks/rows.py" and rows.inputs == ("size",)
    assert join.inputs == ("rows", "size") and join.timeout == 300.0
    assert [n.node for n in lineage(sd.catalog, "toy", "rows")] == ["size", "rows"]
    with pytest.raises(LookupError):
        lineage(sd.catalog, "toy", "nope")


def test_materialize_commit(tmp_repo: Path, tmp_path: Path) -> None:
    (tmp_repo / "nodes").mkdir()
    (tmp_repo / "nodes" / "a.py").write_text("one")
    first = commit_all(tmp_repo)
    (tmp_repo / "nodes" / "a.py").write_text("two")
    commit_all(tmp_repo)
    assert gitinfo.materialize_commit(tmp_repo, first, ["nodes", "gone.py"], tmp_path / "out") == ["gone.py"]
    assert (tmp_path / "out" / "nodes" / "a.py").read_text() == "one"
    with pytest.raises(ValueError):
        gitinfo.materialize_commit(tmp_repo, "0" * 40, ["nodes"], tmp_path / "x")


def test_submit_upload(sd: Env, runner: SingleDocRunner) -> None:
    before = version_count(sd)
    path = upload(sd, "Upload One.pdf")
    run_id = runner.submit(path)
    r = finished(sd, run_id)
    assert r.status == "succeeded", r.error
    assert (r.kind, r.scratch, r.commit) == ("single_doc", False, gitinfo.head_commit(sd.repo))
    doc_id = r.request["doc_id"]
    assert r.request["path"] == str(path.resolve())

    events = sd.catalog.events(run_id)
    assert [ev.kind for ev in events] == ["queued", "doc_started", *["node_finished"] * 3, "doc_finished"]
    nodes = [ev.payload for ev in events if ev.kind == "node_finished"]
    assert [p["node"] for p in nodes] == ["size", "rows", "join"]
    assert nodes[1]["metrics"]["records"] == 2 and not any(p["cached"] for p in nodes)
    assert events[-1].payload["doc_id"] == doc_id
    assert (sd.config.run_dir(run_id) / "log.jsonl").exists()

    chain = lineage(sd.catalog, "toy", "join")
    entries = {n.node: sd.catalog.cache_get(n.node_fp, doc_id) for n in chain}
    assert all(entries.values())
    [pending] = sd.catalog.pending_docs()
    assert (pending.doc_id, pending.version_id, pending.run_id) == (doc_id, chain[-1].version_id, run_id)
    assert pending.paths == ("uploads/Upload One.pdf",) and pending.meta["filename"] == "Upload One.pdf"
    assert pending.object_sha == entries["join"].object_sha and pending.pages is None
    assert version_count(sd) == before

    lib = library(sd.catalog, ObjectStore(sd.config.objects_dir), "toy/join")
    docs = {d["doc_id"]: d for d in lib.documents.to_pylist()}
    assert docs[doc_id]["upload"] and docs[doc_id]["pending"] and len(docs) == 7
    assert lib.rows.filter(pa.compute.equal(lib.rows.column("doc_id"), doc_id)).num_rows == 2

    # Resubmitting reuses every cached node.
    again = runner.submit(path)
    assert finished(sd, again).status == "succeeded"
    assert all(ev.payload["cached"] for ev in sd.catalog.events(again) if ev.kind == "node_finished")

    # Removal hides it; uploading it again brings it back.
    sd.catalog.add_removed_doc(doc_id)
    assert doc_id not in library(sd.catalog, ObjectStore(sd.config.objects_dir), "toy/join").documents.column("doc_id").to_pylist()
    assert finished(sd, runner.submit(path)).status == "succeeded"
    assert doc_id not in sd.catalog.removed_docs()

    # A following full run after a sync reuses the upload's outputs.
    sync(sd.catalog, sd.config.sources["proposals"])
    result = run(sd.config, RunRequest(dag="toy"))
    assert result.status == "succeeded"
    for node, vid in result.versions.items():
        assert sd.catalog.version_doc(vid, doc_id).cached, node
        assert sd.metrics(result.run_id, node)["docs_processed"] == 0


def test_submit_outside_source(sd: Env, runner: SingleDocRunner, tmp_path: Path) -> None:
    stray = tmp_path / "stray.pdf"
    stray.write_bytes((FIXTURES / SZEKERES).read_bytes())
    with pytest.raises(ValueError, match="not in a folder"):
        runner.submit(stray)


def test_script_hash_mismatch(sd: Env) -> None:
    v = sd.catalog.current_version("toy", "rows")
    conn = sqlite3.connect(sd.config.catalog_path)
    with conn:
        conn.execute("UPDATE versions SET meta = ? WHERE id = ?", (json.dumps({**v.meta, "script_hash": "f" * 64}), v.id))
    conn.close()
    r = SingleDocRunner(sd.config, dag="toy", node="join")
    with pytest.raises(RuntimeError, match=r"nodes/rows.py at commit .* has hash"):
        r.start()
    r.close()


def test_faults_fail_only_their_doc(sd: Env, runner: SingleDocRunner) -> None:
    ids = [runner.submit(upload(sd, f"{name}.pdf")) for name in ("hang-me", "crash-me", "raise-me", "fine")]
    runs = [finished(sd, i) for i in ids]
    assert [r.status for r in runs] == ["failed", "failed", "failed", "succeeded"]
    failures = [next(ev.payload for ev in sd.catalog.events(i) if ev.kind == "doc_failed") for i in ids[:3]]
    assert [f["error_type"] for f in failures] == ["Timeout", "WorkerCrashed", "ValueError"]
    assert all(f["node"] == "size" for f in failures)
    assert "cannot size" in failures[2]["message"]
    assert [p.run_id for p in sd.catalog.pending_docs()] == [ids[3]]


def test_close_cancels_unfinished(sd: Env) -> None:
    runner = SingleDocRunner(sd.config, dag="toy", node="join")
    runner.start()
    ids = [runner.submit(upload(sd, f"{name}.pdf")) for name in ("hang-me", "fine")]
    deadline = time.monotonic() + 30
    while sd.catalog.get_run(ids[0]).status != "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    runner.close()
    for i in ids:
        r = sd.catalog.get_run(i)
        assert (r.status, r.error) == ("cancelled", "runner closed")
        assert sd.catalog.events(i)[-1].payload["error_type"] == "Cancelled"
    with pytest.raises(RuntimeError):
        runner.submit(sd.config.uploads_dir / "fine.pdf")


def test_reload_after_promotion(sd: Env, runner: SingleDocRunner) -> None:
    first = finished(sd, runner.submit(upload(sd, "Upload.pdf")))
    doc_id = first.request["doc_id"]
    assert docs_to_reprocess(sd.catalog, sd.config, "toy", "join") == []

    write_dag(sd.repo, DAG.replace("{n: 2}", "{n: 3}"))
    commit_all(sd.repo)
    versions = full_run(sd)
    promote(sd.catalog, sd.config, versions["rows"])
    promote(sd.catalog, sd.config, versions["join"])
    assert docs_to_reprocess(sd.catalog, sd.config, "toy", "join") == [doc_id]
    sd.catalog.add_removed_doc(doc_id)
    assert docs_to_reprocess(sd.catalog, sd.config, "toy", "join") == []
    sd.catalog.restore_removed_doc(doc_id)

    runner.reload()
    second = finished(sd, runner.submit(sd.config.uploads_dir / "Upload.pdf"))
    assert second.status == "succeeded", second.error
    cached = {ev.payload["node"]: ev.payload["cached"] for ev in sd.catalog.events(second.id) if ev.kind == "node_finished"}
    assert cached == {"size": True, "rows": False, "join": False}
    assert {p.version_id for p in sd.catalog.pending_docs() if p.run_id == second.id} == {versions["join"]}
    assert docs_to_reprocess(sd.catalog, sd.config, "toy", "join") == []
    lib = library(sd.catalog, ObjectStore(sd.config.objects_dir), "toy/join")
    assert lib.rows.filter(pa.compute.equal(lib.rows.column("doc_id"), doc_id)).num_rows == 3


@pytest.mark.models
def test_default_dag_upload(tmp_repo: Path, tmp_path: Path) -> None:
    primary = tmp_path / "proposals"
    (primary / SZEKERES).parent.mkdir(parents=True)
    shutil.copy(FIXTURES / SZEKERES, primary / SZEKERES)
    write_config(tmp_repo, primary)
    shutil.copytree(REPO / "dags", tmp_repo / "dags")
    shutil.copytree(REPO / "nodes", tmp_repo / "nodes", ignore=shutil.ignore_patterns("__pycache__"))
    commit_all(tmp_repo)
    assert git(tmp_repo, "status", "--porcelain") == ""
    e = Env(tmp_repo)
    runner = SingleDocRunner(e.config)
    try:
        promote_all(e, full_run(e, "default"))
        runner.start()
        r = finished(e, runner.submit(upload(e, "PROPOSAL ADDENDUM.pdf", AGUILAR, salt=False)), timeout=900)
        assert r.status == "succeeded", r.error
        doc_id = r.request["doc_id"]
        metrics = {ev.payload["node"]: ev.payload["metrics"] for ev in e.catalog.events(r.id) if ev.kind == "node_finished"}
        assert metrics["ocr"]["pages"] >= 1 and metrics["embed"]["vectors"] >= 1

        lib = library(e.catalog, ObjectStore(e.config.objects_dir))
        doc = next(d for d in lib.documents.to_pylist() if d["doc_id"] == doc_id)
        assert doc["upload"] and doc["pending"] and doc["pages"] == metrics["ocr"]["pages"]
        rows = lib.rows.filter(pa.compute.equal(lib.rows.column("doc_id"), doc_id))
        assert rows.num_rows == metrics["embed"]["vectors"]
        assert lib.rows.schema.field("vector").type == pa.list_(pa.float32(), 1024)
        vectors = np.array(rows.column("vector").to_pylist(), dtype=np.float32)
        assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-3)
        assert any(t.strip() for t in rows.column("text").to_pylist())
    finally:
        runner.close()
        e.catalog.close()


def test_start_fails_runs_left_by_a_previous_runner(sd: Env) -> None:
    stale = sd.catalog.create_run("toy", {"path": "uploads/x.pdf"}, scratch=False, commit=None, dirty=False, kind="single_doc")
    dag_run = sd.catalog.create_run("toy", {"nodes": ["size"]}, scratch=False, commit=None, dirty=False)
    runner = SingleDocRunner(sd.config, dag="toy", node="join")
    runner.start()
    try:
        assert (sd.catalog.get_run(stale).status, sd.catalog.get_run(stale).error) == ("failed", "single-doc runner restarted")
        assert sd.catalog.get_run(dag_run).status == "queued"
    finally:
        runner.close()
