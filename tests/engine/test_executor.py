from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from engine_util import SZEKERES, TOY_DAG, Env, commit_all, env, write_dag  # noqa: F401

from ingest import obs
from ingest.engine.executor import RunResult, cancel, run, submit
from ingest.engine.planner import RunRequest
from ingest.state import promotable, promote
from ingest.store.objects import ObjectStore

META_KEYS = {"script", "script_hash", "params", "env", "describe", "checks", "checks_fp", "timeout", "workers", "code"}
STATS_KEYS = {"docs", "ok", "error", "skipped", "cached", "processed", "size_bytes", "wall_s", "totals"}

FAULTS_DAG = """\
name: faults
source: proposals
nodes:
  size:
    script: nodes/size.py
    params: {raise_on: Szekeres, exit_on: ELLIOT, sleep_on: AGUILAR, sleep_s: 60}
    workers: 1
    timeout: 3
  rows:
    script: nodes/rows.py
    inputs: [size]
    workers: 1
"""

BROKEN_DAG = """\
name: broken
source: proposals
nodes:
  bad:
    script: nodes/bad_setup.py
    workers: 2
  after:
    script: nodes/rows.py
    inputs: [bad]
    workers: 1
  other:
    script: nodes/size.py
    workers: 1
"""


def wait_for(cond: Callable[[], Any], timeout: float = 30.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if value := cond():
            return value
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def in_thread(fn: Callable[[], RunResult]) -> tuple[threading.Thread, dict[str, RunResult]]:
    out: dict[str, RunResult] = {}
    t = threading.Thread(target=lambda: out.setdefault("result", fn()))
    t.start()
    return t, out


def add_dag(env: Env, text: str, name: str) -> None:
    write_dag(env.repo, text, name)
    commit_all(env.repo)


def test_full_run_and_rerun(env: Env) -> None:
    result = env.run()
    assert result.status == "succeeded"
    assert set(result.versions) == {"size", "rows", "total"}
    for node, vid in result.versions.items():
        v = env.catalog.get_version(vid)
        assert v.status == "complete" and v.seq == 1 and not v.scratch and not v.dirty
        assert v.run_id == result.run_id and v.snapshot_id == env.snapshot_id and v.commit
        assert set(v.meta) == META_KEYS and v.meta["code"] == "committed"
        assert set(v.stats) == STATS_KEYS
        assert v.stats["docs"] == v.stats["ok"] == v.stats["processed"] == 6 and v.stats["cached"] == 0
    rows = env.catalog.get_version(result.versions["rows"])
    assert rows.input_versions == {"size": result.versions["size"]}
    assert rows.stats["totals"] == {"records": 12.0}
    assert rows.meta["checks"] == "nodes/checks/rows.py" and rows.meta["checks_fp"]
    objects = ObjectStore(env.config.objects_dir)
    assert rows.stats["size_bytes"] == sum(objects.size(d.object_sha, "parquet") for d in env.docs(rows.id).values())
    for d in env.docs(rows.id).values():
        assert d.kind == "parquet" and d.metrics == {"records": 2.0} and d.duration_s is not None and d.worker_pid
        assert env.catalog.cache_get(rows.node_fp, d.doc_id).object_sha == d.object_sha

    kinds = [e.kind for e in env.catalog.events(result.run_id)]
    assert kinds[:3] == ["run_queued", "run_started", "node_started"] and kinds[-1] == "run_finished"
    assert kinds.count("doc_finished") == 18 and kinds.count("node_finished") == 3
    m = env.metrics(result.run_id, "size")
    assert m["docs_processed"] == 6 and m["docs_cached"] == 0 and m["cache_hit_rate"] == 0 and m["wall_s"] > 0
    assert "model_load_s" in m and m["failure_rate"] == 0
    assert set(env.metrics(result.run_id, "size", env.doc_ids[0])) == {"duration_s", "peak_rss_mb"}
    run_row = env.catalog.get_run(result.run_id)
    assert run_row.status == "succeeded" and run_row.request["nodes"] == ["size", "rows", "total"]

    # Everything cached and unchanged: the latest versions are reused, nothing is processed.
    again = env.run()
    assert again.status == "succeeded" and again.versions == result.versions
    assert env.metrics(again.run_id, "rows")["docs_cached"] == 6
    assert env.metrics(again.run_id, "rows")["docs_processed"] == 0
    assert env.catalog.run_versions(again.run_id) == []


def test_force_recomputes(env: Env) -> None:
    first = env.run()
    forced = env.run(recompute="force")
    assert set(forced.versions.values()).isdisjoint(first.versions.values())
    for node, vid in forced.versions.items():
        v = env.catalog.get_version(vid)
        assert v.stats["processed"] == 6 and v.stats["cached"] == 0 and v.seq == 2
    old, new = env.docs(first.versions["size"]), env.docs(forced.versions["size"])
    fp = env.catalog.get_version(forced.versions["size"]).node_fp
    for doc_id, d in new.items():
        assert env.catalog.cache_get(fp, doc_id).object_sha == d.object_sha
        assert ObjectStore(env.config.objects_dir).exists(old[doc_id].object_sha, "json")


def test_param_change_and_selections(env: Env) -> None:
    first = env.run()
    for vid in first.versions.values():
        promote(env.catalog, env.config, vid)
    write_dag(env.repo, TOY_DAG.replace("{n: 2}", "{n: 3}"))

    only_rows = env.run(selection="node", target="rows")
    assert set(only_rows.versions) == {"rows"}
    rows = env.catalog.get_version(only_rows.versions["rows"])
    assert rows.input_versions == {"size": first.versions["size"]}
    assert rows.stats["processed"] == 6 and rows.stats["totals"]["records"] == 18
    assert rows.scratch and rows.dirty and rows.meta["code"] == "working"
    assert not promotable(env.catalog, rows.id)[0]

    down = env.run(selection="downstream", target="rows")
    assert down.versions["rows"] == rows.id  # unchanged scratch version reused
    total = env.catalog.get_version(down.versions["total"])
    assert total.input_versions == {"rows": rows.id} and total.stats["processed"] == 6

    commit_all(env.repo)
    committed = env.run(selection="downstream", target="rows")
    v = env.catalog.get_version(committed.versions["rows"])
    assert not v.scratch and v.stats["cached"] == 6 and v.stats["processed"] == 0
    assert env.catalog.get_version(committed.versions["total"]).stats["cached"] == 6


def test_input_versions_override(env: Env) -> None:
    first = env.run()
    write_dag(env.repo, TOY_DAG.replace("{salt: 1}", "{salt: 2}"))
    commit_all(env.repo)
    second = env.run(selection="node", target="size")
    promote(env.catalog, env.config, second.versions["size"])
    r = env.run(selection="node", target="rows", input_versions={"size": first.versions["size"]})
    rows = env.catalog.get_version(r.versions["rows"])
    assert rows.input_versions == {"size": first.versions["size"]}
    assert rows.node_fp == env.catalog.get_version(first.versions["rows"]).node_fp
    assert rows.id == first.versions["rows"]  # same inputs and outputs → reused


def test_doc_subset_is_scratch(env: Env) -> None:
    ids = env.doc_ids[:2]
    r = env.run(docs=ids)
    for vid in r.versions.values():
        v = env.catalog.get_version(vid)
        assert v.scratch and not v.dirty and set(env.docs(vid)) == set(ids)
        ok, reasons = promotable(env.catalog, vid)
        assert not ok and any("scratch" in x for x in reasons)
    assert env.catalog.get_run(r.run_id).scratch
    # The full run reuses the subset's cached docs.
    full = env.run()
    assert env.catalog.get_version(full.versions["size"]).stats["cached"] == 2


def test_committed_run_uses_head_scripts(env: Env) -> None:
    script = env.repo / "nodes" / "size.py"
    script.write_text(script.read_text().replace('"pid": os.getpid()}', '"pid": os.getpid(), "marker": 1}'))
    objects = ObjectStore(env.config.objects_dir)

    committed = env.run(code="committed", selection="node", target="size")
    v = env.catalog.get_version(committed.versions["size"])
    assert v.meta["code"] == "committed" and not v.scratch and not v.dirty
    assert all("marker" not in objects.get_json(d.object_sha) for d in env.docs(v.id).values())

    working = env.run(selection="node", target="size")
    w = env.catalog.get_version(working.versions["size"])
    assert w.meta["code"] == "working" and w.scratch and w.dirty and w.node_fp != v.node_fp
    assert all(objects.get_json(d.object_sha)["marker"] == 1 for d in env.docs(w.id).values())


def test_faults_fail_only_their_doc(env: Env) -> None:
    add_dag(env, FAULTS_DAG, "faults")
    start = time.monotonic()
    r = env.run("faults")
    assert time.monotonic() - start < 30
    assert r.status == "succeeded"
    docs = env.docs(r.versions["size"])
    by_path = {p: docs[env.doc_id(p)] for p in ("Szekeres", "ELLIOT", "AGUILAR")}
    assert by_path["Szekeres"].status == "error" and by_path["Szekeres"].error_type == "ValueError"
    assert "cannot size" in by_path["Szekeres"].traceback and "size.py" in by_path["Szekeres"].traceback
    assert by_path["ELLIOT"].error_type == "WorkerCrashed" and "code 3" in by_path["ELLIOT"].error_message
    assert by_path["AGUILAR"].error_type == "Timeout" and by_path["AGUILAR"].duration_s >= 3
    ok = [d for d in docs.values() if d.status == "ok"]
    assert len(ok) == 3
    # One worker in docs order: raise, crash → replaced, ok, timeout → replaced, ok, ok.
    assert by_path["Szekeres"].worker_pid == by_path["ELLIOT"].worker_pid
    assert len({d.worker_pid for d in docs.values()}) == 3
    assert by_path["ELLIOT"].worker_pid not in {d.worker_pid for d in ok}
    v = env.catalog.get_version(r.versions["size"])
    assert v.stats["ok"] == 3 and v.stats["error"] == 3
    m = env.metrics(r.run_id, "size")
    assert m["docs_failed"] == 3 and m["failure_rate"] == 0.5

    rows = env.docs(r.versions["rows"])
    assert sorted(d.status for d in rows.values()) == ["ok"] * 3 + ["skipped"] * 3
    assert rows[env.doc_id("ELLIOT")].error_message == "input size is error"
    # Failed docs are not cached: a rerun retries only them.
    assert env.catalog.cache_get(v.node_fp, env.doc_id("Szekeres")) is None


def test_setup_failure_fails_node_and_run(env: Env) -> None:
    add_dag(env, BROKEN_DAG, "broken")
    r = env.run("broken")
    assert r.status == "failed" and set(r.versions) == {"other"}
    run_row = env.catalog.get_run(r.run_id)
    assert "bad" in run_row.error and "model weights not found" in run_row.error
    bad = [v for v in env.catalog.run_versions(r.run_id) if v.node == "bad"]
    assert len(bad) == 1 and bad[0].status == "abandoned"
    docs = env.docs(bad[0].id).values()
    assert len(docs) == 6 and all(d.status == "error" and d.error_type == "RuntimeError" for d in docs)
    assert all("model weights not found" in d.traceback for d in docs)
    assert not [v for v in env.catalog.run_versions(r.run_id) if v.node == "after"]
    kinds = [e.kind for e in env.catalog.events(r.run_id)]
    assert "node_failed" in kinds and "node_skipped" in kinds


def test_hard_and_script_checks(env: Env) -> None:
    add_dag(
        env,
        "name: checked\nsource: proposals\nnodes:\n"
        "  bad:\n    script: nodes/bad_chunk.py\n    workers: 1\n"
        "  size:\n    script: nodes/size.py\n    workers: 1\n"
        "  rows:\n    script: nodes/rows.py\n    inputs: [size]\n    params: {max_size: 300000}\n"
        "    checks: nodes/checks/rows.py\n    workers: 1\n",
        "checked",
    )
    r = env.run("checked")
    bad = env.catalog.get_version(r.versions["bad"])
    assert bad.status == "complete" and bad.stats["error"] == 6 and bad.artifact_type == "chunk"
    assert all(d.error_type == "HardCheckFailed" and d.object_sha is None for d in env.docs(bad.id).values())
    hard = env.catalog.issues(bad.id)
    assert hard and all(i.source == "hard" and i.level == "error" for i in hard)
    assert {i.code for i in hard} >= {"missing_column"}

    rows = env.catalog.get_version(r.versions["rows"])
    issues = env.catalog.issues(rows.id)
    assert {i.source for i in issues} == {"script"} and all(i.checks_fp == rows.meta["checks_fp"] for i in issues)
    assert sum(1 for i in issues if i.code == "toy_warning") == 6
    big = {i.doc_id for i in issues if i.code == "too_big"}
    docs = env.docs(rows.id)
    assert big and all(docs[d].status == "error" and docs[d].error_type == "CheckFailed" for d in big)
    assert all(docs[d].status == "ok" for d in docs if d not in big)

    again = env.run("checked")
    assert env.catalog.get_version(again.versions["bad"]).stats["processed"] == 6  # hard errors not cached
    assert again.versions["rows"] == rows.id  # script-check errors are cached; unchanged → reused


def test_cached_docs_are_rechecked(env: Env) -> None:
    first = env.run(docs=env.doc_ids[:3])
    full = env.run()
    rows = env.catalog.get_version(full.versions["rows"])
    assert rows.id != first.versions["rows"] and rows.stats["cached"] == 3
    assert sum(1 for i in env.catalog.issues(rows.id) if i.code == "toy_warning") == 6
    assert env.catalog.issues(rows.id, env.doc_ids[0])


def test_logs_and_debug_files(env: Env) -> None:
    add_dag(env, "name: noisy\nsource: proposals\nnodes:\n  noisy:\n    script: nodes/noisy.py\n    workers: 2\n", "noisy")
    r = env.run("noisy")
    run_dir = env.config.run_dir(r.run_id)
    vid = r.versions["noisy"]
    doc = env.doc_ids[0]
    lines = list(obs.read_logs(run_dir, doc_id=doc))
    assert lines and all(line["doc_id"] == doc and line["version_id"] == vid and line["node"] == "noisy" for line in lines)
    by_kind = {(line["kind"], line["message"]) for line in lines}
    assert ("stdout", f"stdout {doc}") in by_kind
    assert ("stderr", f"stderr {doc}") in by_kind
    assert ("stderr", f"fd2 {doc}") in by_kind
    assert ("log", f"log {doc}") in by_kind
    assert ("warning", f"UserWarning: warn {doc}") in by_kind
    assert ("log", f"debug {doc}") not in by_kind
    assert {line["event"] for line in lines if line["kind"] == "event"} >= {"doc_started", "doc_finished"}
    other = env.doc_ids[1]
    assert all(other not in line["message"] for line in lines)
    setup = [line for line in obs.read_logs(run_dir, node="noisy") if line["message"] == "setting up noisy"]
    assert len(setup) == 2 and all("doc_id" not in line and line["version_id"] == vid for line in setup)
    assert obs.list_debug_files(run_dir, "noisy", doc) == []

    scratch = run(env.config, RunRequest(dag="noisy", docs=[doc], recompute="force"), log_level="DEBUG")
    scratch_dir = env.config.run_dir(scratch.run_id)
    files = obs.list_debug_files(scratch_dir, "noisy", doc)
    assert [f.name for f in files] == ["note.json"] and doc in files[0].read_text()
    assert any(line["message"] == f"debug {doc}" for line in obs.read_logs(scratch_dir, doc_id=doc, level="DEBUG"))


def test_describe_meta(env: Env) -> None:
    dag = "name: {}\nsource: proposals\nnodes:\n  d:\n    script: nodes/describe.py\n    params: {{dim: 3}}\n    workers: 1\n"
    add_dag(env, dag.format("d1"), "d1")
    add_dag(env, dag.format("d2"), "d2")
    first = env.run("d1")
    assert env.catalog.get_version(first.versions["d"]).meta["describe"] == {"model": "toy", "dim": 3}
    # Same node in another DAG: everything cached, describe() comes from a describe-only worker.
    other = env.run("d2")
    v = env.catalog.get_version(other.versions["d"])
    assert v.stats["cached"] == 6 and v.meta["describe"] == {"model": "toy", "dim": 3}
    # A scratch subset of cached docs takes describe() from the earlier version.
    sub = env.catalog.get_version(env.run("d1", docs=env.doc_ids[:1]).versions["d"])
    assert sub.scratch and sub.meta["describe"] == {"model": "toy", "dim": 3}


def test_run_corpus(env: Env) -> None:
    add_dag(
        env,
        "name: corpus\nsource: proposals\nnodes:\n"
        "  size:\n    script: nodes/size.py\n    workers: 2\n"
        "  rank:\n    script: nodes/rank.py\n    inputs: [size]\n    workers: 4\n"
        "  follow:\n    script: nodes/follow.py\n    inputs: [rank]\n    workers: 1\n",
        "corpus",
    )
    objects = ObjectStore(env.config.objects_dir)

    def ofs(r: RunResult, node: str) -> set[int]:
        return {objects.get_json(d.object_sha)["of"] for d in env.docs(r.versions[node]).values()}

    full = env.run("corpus")
    rank = env.catalog.get_version(full.versions["rank"])
    assert rank.stats["ok"] == 6 and ofs(full, "rank") == {6} and ofs(full, "follow") == {6}
    assert len({d.worker_pid for d in env.docs(rank.id).values()}) == 1

    sub = env.run("corpus", docs=env.doc_ids[:2])
    assert env.catalog.get_version(sub.versions["rank"]).stats["processed"] == 2
    assert ofs(sub, "rank") == {2} and ofs(sub, "follow") == {2}  # downstream not served stale outputs

    again = env.run("corpus")
    assert again.versions == full.versions


def test_cancel_mid_run(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOY_DELAY", "1")
    thread, out = in_thread(lambda: env.run(selection="node", target="size"))
    run_id = wait_for(lambda: next((r.id for r in env.catalog.list_runs("toy")), None))
    wait_for(lambda: sum(e.kind == "doc_finished" for e in env.catalog.events(run_id)) >= 2)
    cancel(env.catalog, run_id)
    thread.join(30)
    assert out["result"].status == "cancelled" and out["result"].versions == {}
    assert env.catalog.get_run(run_id).status == "cancelled"
    assert [v.status for v in env.catalog.run_versions(run_id)] == ["abandoned"]
    assert env.catalog.list_versions("toy", "size") == []
    finished = sum(e.kind == "doc_finished" for e in env.catalog.events(run_id))

    monkeypatch.delenv("TOY_DELAY")
    again = env.run(selection="node", target="size")
    v = env.catalog.get_version(again.versions["size"])
    assert v.stats["cached"] == finished and v.stats["processed"] == 6 - finished


def test_overlapping_run_waits(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOY_DELAY", "0.5")
    t1, out1 = in_thread(lambda: env.run(selection="node", target="size"))
    first = wait_for(lambda: next((r for r in env.catalog.list_runs("toy") if r.status == "running"), None))
    t2, out2 = in_thread(lambda: env.run())
    second = wait_for(lambda: next((r for r in env.catalog.list_runs("toy") if r.id != first.id), None))
    time.sleep(0.5)
    assert env.catalog.get_run(first.id).status == "running"
    assert env.catalog.get_run(second.id).status == "queued"

    request = RunRequest(dag="toy", selection="node", target="rows", input_versions={})
    with pytest.raises(Exception, match="no current version"):
        submit(env.config, request)
    queued = submit(env.config, RunRequest(dag="toy", selection="upstream", target="rows"))
    assert submit(env.config, RunRequest(dag="toy", selection="upstream", target="rows")) == queued

    t1.join(60)
    t2.join(60)
    a, b = env.catalog.get_run(first.id), env.catalog.get_run(second.id)
    assert out1["result"].status == out2["result"].status == "succeeded"
    assert b.started_at >= a.ended_at
    final = wait_for(lambda: (r := env.catalog.get_run(queued)).status in ("succeeded", "failed") and r, 60)
    assert final.status == "succeeded" and final.started_at >= b.ended_at
    assert (env.config.run_dir(queued) / "log.jsonl").exists()


def test_cancel_queued_submit(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOY_DELAY", "0.5")
    thread, _ = in_thread(lambda: env.run(selection="node", target="size"))
    wait_for(lambda: any(r.status == "running" for r in env.catalog.list_runs("toy")))
    queued = submit(env.config, RunRequest(dag="toy"))
    cancel(env.catalog, queued)
    final = wait_for(lambda: (r := env.catalog.get_run(queued)).status not in ("queued", "running") and r)
    assert final.status == "cancelled" and final.started_at is None
    thread.join(60)


def test_dead_executor_is_reaped(env: Env, tmp_path: Path) -> None:
    run_id = env.catalog.create_run("toy", {"dag": "toy", "nodes": ["size"]}, scratch=False, commit=None, dirty=False)
    env.catalog.add_event(run_id, "run_submitted", {"executor_pid": 2**22 + 12345})
    env.catalog.set_run_status(run_id, "running")
    r = env.run()
    assert r.status == "succeeded"
    assert env.catalog.get_run(run_id).status == "failed"
