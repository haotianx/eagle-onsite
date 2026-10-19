from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from engine_util import NODES, TOY_DAG, Env, commit_all, env, write_dag  # noqa: F401

from ingest.engine.planner import PlanError, RunRequest, plan_run, selected_nodes
from ingest.spec import load_dag
from ingest.state import promote


def plan(e: Env, **kw):
    return plan_run(e.catalog, e.config, RunRequest(dag="toy", **kw))


def test_selected_nodes(env: Env) -> None:
    dag = load_dag(env.repo / "dags" / "toy.yaml", env.repo)
    assert selected_nodes(dag, "all", None) == ["size", "rows", "total"]
    assert selected_nodes(dag, "node", "rows") == ["rows"]
    assert selected_nodes(dag, "downstream", "rows") == ["rows", "total"]
    assert selected_nodes(dag, "upstream", "rows") == ["size", "rows"]
    with pytest.raises(PlanError, match="needs a target"):
        selected_nodes(dag, "node", None)
    with pytest.raises(PlanError, match="unknown node"):
        selected_nodes(dag, "node", "nope")


def test_plan_full_run(env: Env) -> None:
    p = plan(env)
    assert p.code == "committed" and p.commit and not p.dirty
    assert not p.scratch and p.snapshot_id == env.snapshot_id
    assert len(p.doc_ids) == 6
    assert [n.node for n in p.nodes] == ["size", "rows", "total"]
    assert all(n.docs_to_process == 6 and n.docs_cached == 0 for n in p.nodes)
    rows = p.nodes[1]
    assert rows.inputs["size"].version_id is None
    assert rows.inputs["size"].fingerprint == p.nodes[0].fingerprint

    env.run()
    p2 = plan(env)
    assert [n.fingerprint for n in p2.nodes] == [n.fingerprint for n in p.nodes]
    assert all(n.docs_cached == 6 and n.docs_to_process == 0 for n in p2.nodes)
    assert all(n.docs_cached == 0 and n.docs_to_process == 6 for n in plan(env, recompute="force").nodes)


def test_plan_param_change_is_working_tree(env: Env) -> None:
    env.run()
    write_dag(env.repo, TOY_DAG.replace("{n: 2}", "{n: 3}"))
    p = plan(env)
    assert p.code == "working" and p.dirty
    assert p.scratch and p.scratch_reasons == ("working tree",)
    assert [(n.node, n.docs_cached) for n in p.nodes] == [("size", 6), ("rows", 0), ("total", 0)]
    committed = plan(env, code="committed")
    assert committed.code == "committed" and committed.dirty and not committed.scratch
    assert all(n.docs_cached == 6 for n in committed.nodes)


def test_plan_inputs_outside_selection(env: Env) -> None:
    with pytest.raises(PlanError, match="no current version"):
        plan(env, selection="node", target="rows")
    result = env.run()
    with pytest.raises(PlanError, match="no current version"):
        plan(env, selection="node", target="rows")
    p = plan(env, selection="node", target="rows", input_versions={"size": result.versions["size"]})
    assert p.nodes[0].inputs["size"].version_id == result.versions["size"]
    promote(env.catalog, env.config, result.versions["size"])
    p = plan(env, selection="node", target="rows")
    assert p.nodes[0].inputs["size"].version_id == result.versions["size"]
    assert p.nodes[0].docs_cached == 6
    with pytest.raises(PlanError, match="not toy/size"):
        plan(env, selection="node", target="rows", input_versions={"size": result.versions["rows"]})
    with pytest.raises(PlanError, match="does not read from outside"):
        plan(env, input_versions={"size": result.versions["size"]})


def test_plan_docs(env: Env) -> None:
    p = plan(env, docs=env.doc_ids[:2])
    assert p.doc_ids == tuple(env.doc_ids[:2]) and p.scratch_reasons == ("doc subset",)
    sample = plan(env, docs=3, sample_seed=1)
    assert len(sample.doc_ids) == 3 and sample.scratch
    assert sample.doc_ids == plan(env, docs=3, sample_seed=1).doc_ids
    assert not plan(env, docs=100).scratch
    assert not plan(env, docs=list(env.doc_ids)).scratch
    with pytest.raises(PlanError, match="not in the snapshot"):
        plan(env, docs=["nope"])


def test_plan_errors(env: Env) -> None:
    with pytest.raises(PlanError, match="unknown DAG"):
        plan_run(env.catalog, env.config, RunRequest(dag="nope"))
    with pytest.raises(PlanError, match="not found"):
        plan(env, snapshot_id=99)


def test_committed_run_needs_a_commit(tmp_repo: Path) -> None:
    shutil.copytree(NODES, tmp_repo / "nodes")
    write_dag(tmp_repo, TOY_DAG)
    e = Env(tmp_repo)
    try:
        with pytest.raises(PlanError, match="needs a git commit"):
            plan(e, code="committed")
        p = plan(e)
        assert p.code == "working" and p.commit is None and p.scratch_reasons == ("working tree",)
        commit_all(tmp_repo)
        assert plan(e).code == "committed"
    finally:
        e.catalog.close()


def test_dirty_nodes_are_per_node(env: Env) -> None:
    from ingest.engine.planner import dirty_nodes

    dag = lambda: load_dag(env.repo / "dags" / "toy.yaml", env.repo)  # noqa: E731
    assert dirty_nodes(env.repo, dag()) == set()
    write_dag(env.repo, TOY_DAG.replace("params: {n: 2}", "params: {n: 3}"))
    assert dirty_nodes(env.repo, dag()) == {"rows"}
    p = plan(env, selection="node", target="size")
    assert p.code == "committed" and not p.dirty and not p.scratch
    assert plan(env, selection="upstream", target="rows").scratch
    (env.repo / "nodes" / "total.py").write_text((env.repo / "nodes" / "total.py").read_text() + "\n")
    assert dirty_nodes(env.repo, dag()) == {"rows", "total"}
    write_dag(env.repo, TOY_DAG.replace("source: proposals", "source: other"))
    assert dirty_nodes(env.repo, dag()) == {"size", "rows", "total"}
