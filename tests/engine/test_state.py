from __future__ import annotations

import pyarrow.parquet as pq
import pytest
from engine_util import TOY_DAG, Env, commit_all, env, write_dag  # noqa: F401

from ingest.sources import sync
from ingest.spec import load_dag
from ingest.state import node_states, promotable, promote


def states(e: Env) -> dict[str, tuple[str, tuple[str, ...]]]:
    dag = load_dag(e.repo / "dags" / "toy.yaml", e.repo)
    return {n: (s.state, s.reasons) for n, s in node_states(e.catalog, e.config, dag).items()}


def test_states_through_a_change(env: Env) -> None:
    assert {s for s, _ in states(env).values()} == {"never_run"}
    first = env.run()
    assert {s for s, _ in states(env).values()} == {"candidate"}
    assert promote(env.catalog, env.config, first.versions["size"]) == []
    assert promote(env.catalog, env.config, first.versions["rows"]) == ["6 warning issue(s)"]
    assert promote(env.catalog, env.config, first.versions["total"]) == []
    assert states(env) == {n: ("fresh", ()) for n in ("size", "rows", "total")}

    write_dag(env.repo, TOY_DAG.replace("{n: 2}", "{n: 3}"))
    dag = load_dag(env.repo / "dags" / "toy.yaml", env.repo)
    full = node_states(env.catalog, env.config, dag)
    assert full["rows"].dirty and full["rows"].fingerprint != full["rows"].current.node_fp
    assert states(env) == {
        "size": ("fresh", ()),
        "rows": ("stale", ("code or params changed",)),
        "total": ("stale", ("input rows is stale",)),
    }

    commit_all(env.repo)
    second = env.run(selection="downstream", target="rows")
    s = node_states(env.catalog, env.config, dag)
    assert [v.id for v in s["rows"].candidates] == [second.versions["rows"]]
    assert s["rows"].state == "stale"

    # Promoting rows alone leaves total stale: its current was built from the old rows.
    assert promote(env.catalog, env.config, second.versions["rows"]) == ["6 warning issue(s)"]
    assert states(env)["rows"] == ("fresh", ())
    assert states(env)["total"] == ("stale", ("input rows is not current",))
    warnings = promote(env.catalog, env.config, second.versions["total"])
    assert warnings == []
    assert states(env) == {n: ("fresh", ()) for n in ("size", "rows", "total")}

    # Promoting an old total again warns that it was built from a rows version that isn't current.
    warnings = promote(env.catalog, env.config, first.versions["total"])
    assert len(warnings) == 1 and "toy/rows@v1" in warnings[0] and "toy/rows@v2" in warnings[0]
    assert states(env)["total"] == ("stale", ("input rows is not current",))


def test_new_snapshot_makes_nodes_stale(env: Env, fixtures_dir, tmp_path) -> None:
    import shutil

    from ingest.config import SourceConfig

    first = env.run()
    for vid in first.versions.values():
        promote(env.catalog, env.config, vid)
    src = shutil.copytree(fixtures_dir, tmp_path / "src")
    (src / "3002 ELLIOT" / "CONTRACT" / "Mike Proposal_Elliot Pickle Ball.pdf").unlink()
    sync(env.catalog, SourceConfig(name="proposals", root=src))
    assert states(env)["size"] == ("stale", ("snapshot is not the latest",))


def test_failed_and_running_states(env: Env) -> None:
    write_dag(env.repo, TOY_DAG.replace("nodes/size.py", "nodes/bad_setup.py"))
    commit_all(env.repo)
    r = env.run()
    assert r.status == "failed"
    got = states(env)
    assert got["size"][0] == got["rows"][0] == "failed"
    run_id = env.catalog.create_run("toy", {"dag": "toy", "nodes": ["rows"]}, scratch=False, commit=None, dirty=False)
    env.catalog.set_run_status(run_id, "running")
    assert states(env)["rows"][0] == "running"


def test_promote_rules_and_publish(env: Env) -> None:
    scratch = env.run(docs=env.doc_ids[:2])
    ok, reasons = promotable(env.catalog, scratch.versions["rows"])
    assert not ok and reasons == ["scratch version (doc subset or working tree)"]
    with pytest.raises(ValueError, match="not promotable"):
        promote(env.catalog, env.config, scratch.versions["rows"])

    full = env.run()
    promote(env.catalog, env.config, full.versions["size"])
    assert not (env.config.published_dir / "toy" / "size.parquet").exists()
    promote(env.catalog, env.config, full.versions["rows"])
    table = pq.read_table(env.config.published_dir / "toy" / "rows.parquet")
    assert table.num_rows == 12 and {"doc_id", "paths", "filename"} <= set(table.column_names)
    assert promotable(env.catalog, full.versions["rows"]) == (False, ["already current"])
    assert env.catalog.current_version("toy", "total") is None
