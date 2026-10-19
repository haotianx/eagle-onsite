"""Dev seeding (synthetic) and the Phase 1 fakes used by the dev server."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import MappingProxyType

import pytest

import fakes
import seed
from ingest import obs, sources, validation
from ingest.config import Config, SourceConfig
from ingest.contract import NodeContext
from ingest.engine import executor, planner
from ingest.engine.planner import RunRequest
from ingest.spec import DagSpec, NodeSpec, load_dags
from ingest.state import NodeState
from ingest.store.catalog import Catalog

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        repo_root=REPO,
        data_dir=tmp_path / "data",
        sources=MappingProxyType({"proposals": SourceConfig("proposals", seed.FIXTURES)}),
    )


@pytest.fixture
def seeded(config: Config) -> dict:
    return seed.seed(config, real=False)


@pytest.fixture
def catalog(config: Config, seeded: dict):
    c = Catalog(config.catalog_path)
    yield c
    c.close()


@pytest.fixture
def installed(config: Config, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from ingest import state

    for module, name in ((state, "node_states"), (state, "promotable"), (state, "promote"),
                         (planner, "selected_nodes"), (planner, "plan_run"), (executor, "submit"), (executor, "cancel")):
        monkeypatch.setattr(module, name, getattr(module, name))
    return fakes.install(config)


def dag(config: Config) -> DagSpec:
    return load_dags(config.repo_root, config.sources)["default"]


def test_seed_snapshots_diff(catalog: Catalog, seeded: dict) -> None:
    s1, s2 = seeded["snapshots"]
    assert catalog.latest_snapshot("proposals").id == s2
    assert catalog.get_snapshot(s2).doc_count == 6 and catalog.get_snapshot(s2).file_count == 7
    diff = sources.diff_snapshots(catalog, s1, s2)
    assert len(diff.added) == 1 and len(diff.removed) == 1
    removed = catalog.snapshot_doc(s1, diff.removed[0])
    assert removed.flags == ("corrupt",) and removed.paths == (seed.TRUNCATED,)
    assert catalog.get_snapshot(s1).created_at < catalog.get_snapshot(s2).created_at


def test_seed_versions(config: Config, catalog: Catalog, seeded: dict) -> None:
    v = seeded["versions"]
    current = catalog.current_versions("default")
    assert {n: c.id for n, c in current.items()} == {"ocr": v["ocr@v1"], "chunk": v["chunk@v1"], "embed": v["embed@v1"]}
    for key in ("chunk@v2", "embed@v2"):
        row = catalog.get_version(v[key])
        assert row.status == "complete" and not row.scratch and not row.dirty
    assert catalog.get_version(v["embed@v2"]).input_versions == {"chunk": v["chunk@v2"]}
    assert catalog.get_version(v["chunk@v2"]).meta["params"]["max_tokens"] == 256
    assert (config.published_dir / "default" / "embed.parquet").is_file()

    embed = catalog.get_version(v["embed@v1"])
    assert set(embed.meta) == {"script", "script_hash", "params", "env", "describe", "checks_fp"}
    assert set(embed.stats) == {"docs", "ok", "error", "skipped", "cached", "processed", "size_bytes", "wall_s", "totals"}
    assert embed.stats["ok"] == 6 and embed.stats["totals"]["vectors"] > 0
    assert {i.source for i in catalog.issues(v["chunk@v2"])} >= {"script"}
    assert any(i.source == "quality" for i in catalog.issues(v["ocr@v1"]))


def test_seed_scratch_run(config: Config, catalog: Catalog, seeded: dict) -> None:
    scratch = catalog.get_version(seeded["versions"]["ocr@v2"])
    assert scratch.scratch and scratch.snapshot_id == seeded["snapshots"][0]
    docs = catalog.version_docs(scratch.id)
    failed = [d for d in docs if d.status == "error"]
    assert len(docs) == 3 and len(failed) == 1
    assert failed[0].error_type == "ValueError" and "flagged corrupt" in failed[0].error_message
    assert "Traceback" in failed[0].traceback
    ok = next(d for d in docs if d.status == "ok")
    run_dir = config.run_dir(seeded["runs"]["scratch"])
    assert [p.name for p in obs.list_debug_files(run_dir, "ocr", ok.doc_id)] == ["pages.json", "summary.txt"]


def test_seed_runs(config: Config, catalog: Catalog, seeded: dict) -> None:
    runs = {name: catalog.get_run(i) for name, i in seeded["runs"].items()}
    assert {n: r.status for n, r in runs.items()} == {
        "full": "succeeded", "failed": "failed", "candidate": "succeeded", "scratch": "succeeded", "cancelled": "cancelled",
    }
    assert runs["scratch"].scratch and runs["failed"].error.startswith("embed: setup failed")
    assert runs["cancelled"].cancel_requested
    assert all(r.request["nodes"] for r in runs.values())
    created = [r.created_at for r in runs.values()]
    assert created == sorted(created) and all(r.started_at <= r.ended_at for r in runs.values())

    kinds = [e.kind for e in catalog.events(runs["full"].id)]
    assert kinds[:2] == ["run_queued", "run_started"] and kinds[-1] == "run_finished"
    assert kinds.count("node_started") == 3 and kinds.count("doc_finished") == 18
    ts = [e.ts for e in catalog.events(runs["full"].id)]
    assert ts == sorted(ts) and ts[0] == runs["full"].created_at
    assert [e.kind for e in catalog.events(runs["cancelled"].id)].count("doc_finished") == 2

    names = {(m.node, m.name) for m in catalog.run_metrics(runs["full"].id) if m.doc_id is None}
    assert {("ocr", "pages_per_s"), ("embed", "model_load_s"), ("chunk", "cache_hit_rate")} <= names
    lines = list(obs.read_logs(config.run_dir(runs["full"].id)))
    assert {line["kind"] for line in lines} >= {"event", "log", "stderr"}
    assert all("node" in line and "doc_id" in line for line in lines if line["kind"] != "event")
    assert json.loads(json.dumps(lines))


def test_is_stub() -> None:
    def stub(x: int) -> int:
        """Doc."""
        raise NotImplementedError

    def real(x: int) -> int:
        if x:
            raise NotImplementedError
        return x

    assert fakes.is_stub(stub) and not fakes.is_stub(real)


def test_install_replaces_only_stubs(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    from ingest import state

    monkeypatch.setattr(state, "promote", lambda *a: [])
    monkeypatch.setattr(state, "node_states", state.node_states)
    for module, name in ((state, "promotable"), (planner, "selected_nodes"), (planner, "plan_run"),
                         (executor, "submit"), (executor, "cancel")):
        monkeypatch.setattr(module, name, getattr(module, name))
    stubbed = fakes.is_stub(state.node_states)
    installed = fakes.install(config)
    assert "ingest.state.promote" not in installed and state.promote is not fakes.promote
    assert ("ingest.state.node_states" in installed) == stubbed
    if stubbed:
        assert state.node_states is fakes.node_states


def test_node_states(config: Config, catalog: Catalog, seeded: dict) -> None:
    states = fakes.node_states(catalog, config, dag(config))
    assert {n: s.state for n, s in states.items()} == {"ocr": "fresh", "chunk": "candidate", "embed": "candidate"}
    assert [v.id for v in states["chunk"].candidates] == [seeded["versions"]["chunk@v2"]]
    assert [v.id for v in states["ocr"].scratch] == [seeded["versions"]["ocr@v2"]]
    assert states["ocr"].fingerprint == states["ocr"].current.node_fp

    fakes.promote(catalog, config, seeded["versions"]["chunk@v2"])
    states = fakes.node_states(catalog, config, dag(config))
    assert states["chunk"].state == "stale" and "code or params changed" in states["chunk"].reasons
    assert states["embed"].state == "stale" and "input chunk is not current" in states["embed"].reasons

    run_id = catalog.create_run("default", {"nodes": ["embed"]}, scratch=False, commit=None, dirty=False)
    catalog.set_run_status(run_id, "failed", "boom")
    assert fakes.node_states(catalog, config, dag(config))["embed"].state == "failed"
    run_id = catalog.create_run("default", {"nodes": ["embed"]}, scratch=False, commit=None, dirty=False)
    catalog.set_run_status(run_id, "running")
    assert fakes.node_states(catalog, config, dag(config))["embed"].state == "running"

    d = dag(config)
    extra = DagSpec(d.name, d.source, {**d.nodes, "summary": NodeSpec("summary", "nodes/embed.py", {}, ("chunk",))}, d.path)
    assert fakes.node_states(catalog, config, extra)["summary"].state == "never_run"
    assert isinstance(states["ocr"], NodeState)


def test_promote(config: Config, catalog: Catalog, seeded: dict) -> None:
    v = seeded["versions"]
    assert fakes.promotable(catalog, v["ocr@v2"]) == (False, ["scratch version"])
    assert fakes.promotable(catalog, v["embed@v1"]) == (False, ["already current"])
    assert fakes.promotable(catalog, v["embed@v2"]) == (True, [])

    published = config.published_dir / "default" / "embed.parquet"
    before = published.stat().st_mtime_ns
    warnings = fakes.promote(catalog, config, v["embed@v2"])
    assert "input chunk is not current" in warnings
    assert catalog.current_version("default", "embed").id == v["embed@v2"]
    assert published.stat().st_mtime_ns != before
    with pytest.raises(ValueError):
        fakes.promote(catalog, config, v["ocr@v2"])


def test_plan_run(config: Config, catalog: Catalog, seeded: dict, installed: list[str]) -> None:
    plan = planner.plan_run(catalog, config, RunRequest(dag="default"))
    assert [(p.node, p.docs_cached, p.docs_to_process) for p in plan.nodes] == [("ocr", 6, 0), ("chunk", 6, 0), ("embed", 6, 0)]
    assert plan.snapshot_id == seeded["snapshots"][1] and len(plan.doc_ids) == 6

    plan = planner.plan_run(catalog, config, RunRequest(dag="default", target="chunk", selection="downstream", recompute="force"))
    assert [(p.node, p.docs_cached, p.docs_to_process) for p in plan.nodes] == [("chunk", 0, 6), ("embed", 0, 6)]
    assert plan.nodes[0].inputs["ocr"].version_id == seeded["versions"]["ocr@v1"]

    plan = planner.plan_run(catalog, config, RunRequest(dag="default", target="embed", selection="node",
                                                        input_versions={"chunk": seeded["versions"]["chunk@v2"]}))
    assert plan.nodes[0].fingerprint == catalog.get_version(seeded["versions"]["embed@v2"]).node_fp
    assert plan.nodes[0].docs_cached == 6

    plan = planner.plan_run(catalog, config, RunRequest(dag="default", docs=2))
    assert plan.scratch and "doc subset" in plan.scratch_reasons and len(plan.doc_ids) == 2
    with pytest.raises(planner.PlanError):
        planner.plan_run(catalog, config, RunRequest(dag="nope"))
    assert fakes.selected_nodes(dag(config), "upstream", "embed") == ["ocr", "chunk", "embed"]


def wait_terminal(catalog: Catalog, run_id: int, timeout: float = 10.0) -> str:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        status = catalog.get_run(run_id).status
        if status in ("succeeded", "failed", "cancelled"):
            return status
        time.sleep(0.05)
    raise TimeoutError


def test_submit_and_cancel(config: Config, catalog: Catalog, seeded: dict, installed: list[str],
                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fakes, "RUN_SECONDS", 0.3)
    run_id = executor.submit(config, RunRequest(dag="default", target="chunk", selection="downstream", recompute="force"))
    assert catalog.get_run(run_id).request["nodes"] == ["chunk", "embed"]
    assert wait_terminal(catalog, run_id) == "succeeded", catalog.get_run(run_id).error
    kinds = [e.kind for e in catalog.events(run_id)]
    assert kinds[:2] == ["run_queued", "run_started"] and kinds[-1] == "run_finished"
    assert kinds.count("doc_finished") == 12 and kinds.count("node_finished") == 2

    monkeypatch.setattr(fakes, "RUN_SECONDS", 5.0)
    run_id = executor.submit(config, RunRequest(dag="default", recompute="force"))
    time.sleep(0.8)
    executor.cancel(catalog, run_id)
    assert wait_terminal(catalog, run_id) == "cancelled"


@pytest.mark.models
def test_real_nodes_smallest_doc(config: Config) -> None:
    obs.configure_environment(offline=True)
    d = dag(config)
    nodes = seed._RealNodes(config.repo_root, d)
    catalog = Catalog(config.catalog_path)
    snapshot = sources.sync(catalog, config.sources["proposals"]).snapshot_id
    doc = next(x for x in sources.snapshot_docs(catalog, snapshot, config.sources["proposals"]) if x.paths[0] == seed.SCRATCH_DOCS[0])
    catalog.close()
    inputs: dict = {}
    for name in d.topo_order():
        params = d.nodes[name].params
        state, _ = nodes.setup(name, params)
        ctx = NodeContext(dag="default", node=name, params=params, state=state)
        res = nodes.run(name, doc, {i: inputs[i] for i in d.nodes[name].inputs}, ctx,
                        {"run_id": 1, "dag": "default", "version_id": None, "doc_id": doc.id})
        assert res.error is None, res.traceback
        atype = nodes.artifact_type(name)
        upstream = {i: inputs[i] for i in d.nodes[name].inputs}
        assert validation.hard_checks(atype, res.record, params, inputs=upstream, meta=nodes.describe(name, params)) == []
        inputs[name] = res.record
    assert inputs["embed"].num_rows > 0
