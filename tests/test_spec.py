from __future__ import annotations

from pathlib import Path

import pytest

from ingest.spec import (
    DEFAULT_TIMEOUT_S,
    DEFAULT_WORKERS,
    DagError,
    dag_paths,
    load_dag,
    load_dags,
    parse_dag,
    validate_dag,
)

RUN = "def run(doc, inputs, params, ctx):\n    return {}\n"

DAG = """\
name: demo
source: proposals
nodes:
  parse:
    script: nodes/parse.py
    params: { engine: x, nested: { list: [1, 2.5, true, null] } }
    timeout: 60
  side:
    script: nodes/parse.py
  chunk:
    script: nodes/chunk.py
    inputs: [parse]
    workers: 2
    checks: nodes/checks/chunk.py
  merge:
    script: nodes/merge.py
    inputs: [chunk, side]
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def repo(tmp_repo: Path) -> Path:
    write(tmp_repo / "nodes" / "parse.py", RUN)
    write(tmp_repo / "nodes" / "chunk.py", RUN)
    write(
        tmp_repo / "nodes" / "merge.py",
        "def run_corpus(docs, inputs, params, ctx):\n    return {}\n",
    )
    write(
        tmp_repo / "nodes" / "checks" / "chunk.py", "def validate(record, params):\n    return []\n"
    )
    write(tmp_repo / "dags" / "demo.yaml", DAG)
    return tmp_repo


def test_parse_dag(repo: Path) -> None:
    dag = parse_dag(repo / "dags" / "demo.yaml", repo)
    assert dag.name == "demo"
    assert dag.source == "proposals"
    assert dag.path == "dags/demo.yaml"
    assert list(dag.nodes) == ["parse", "side", "chunk", "merge"]
    parse = dag.nodes["parse"]
    assert parse.script == "nodes/parse.py"
    assert parse.params == {"engine": "x", "nested": {"list": [1, 2.5, True, None]}}
    assert parse.inputs == ()
    assert parse.timeout == 60.0
    assert parse.workers == DEFAULT_WORKERS
    chunk = dag.nodes["chunk"]
    assert chunk.inputs == ("parse",)
    assert chunk.checks == "nodes/checks/chunk.py"
    assert chunk.workers == 2
    assert chunk.timeout == DEFAULT_TIMEOUT_S
    assert dag.nodes["side"].params == {}


def test_graph_queries(repo: Path) -> None:
    dag = load_dag(repo / "dags" / "demo.yaml", repo, ["proposals"])
    assert dag.topo_order() == ["parse", "side", "chunk", "merge"]
    assert dag.roots() == ["parse", "side"]
    assert dag.upstream("merge") == ["parse", "side", "chunk"]
    assert dag.upstream("parse") == []
    assert dag.downstream("parse") == ["chunk", "merge"]
    assert dag.downstream("merge") == []
    assert dag.node_files("chunk") == ["dags/demo.yaml", "nodes/chunk.py", "nodes/checks/chunk.py"]
    assert dag.node_files("parse") == ["dags/demo.yaml", "nodes/parse.py"]


def test_topo_order_ties_follow_yaml_order(repo: Path) -> None:
    write(
        repo / "dags" / "demo.yaml",
        "name: demo\nsource: proposals\nnodes:\n"
        "  late: {script: nodes/chunk.py, inputs: [b]}\n"
        "  b: {script: nodes/parse.py}\n"
        "  a: {script: nodes/parse.py}\n",
    )
    dag = load_dag(repo / "dags" / "demo.yaml", repo)
    assert dag.topo_order() == ["b", "late", "a"]


def test_real_default_dag_parses() -> None:
    root = Path(__file__).resolve().parents[1]
    dag = parse_dag(root / "dags" / "default.yaml", root)
    assert dag.topo_order() == ["ocr", "chunk", "embed"]
    assert dag.nodes["embed"].workers == 1


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("name: [unclosed\n", "invalid YAML"),
        ("- a\n", "mapping"),
        ("", "mapping"),
        ("source: s\nnodes: {a: {script: x.py}}\n", "missing name"),
        ("name: d\nnodes: {a: {script: x.py}}\n", "missing source"),
        ("name: d\nsource: s\n", "missing nodes"),
        ("name: d\nsource: s\nnodes: {}\n", "non-empty mapping"),
        ("name: d\nsource: s\nextra: 1\nnodes: {a: {script: x.py}}\n", "unknown keys"),
        ("name: d\nsource: s\nnodes: {a: {script: x.py, retries: 2}}\n", "unknown keys"),
        ("name: d\nsource: s\nnodes: {a: {params: {}}}\n", "missing script"),
        ("name: 3\nsource: s\nnodes: {a: {script: x.py}}\n", "name must be"),
        ("name: d\nsource: s\nnodes: {a: {script: x.py, inputs: b}}\n", "inputs must be"),
        ("name: d\nsource: s\nnodes: {a: {script: x.py, inputs: [b, b]}}\n", "duplicate inputs"),
        ("name: d\nsource: s\nnodes: {a: {script: x.py, params: [1]}}\n", "params must be"),
        ("name: d\nsource: s\nnodes: {a: {script: x.py, timeout: soon}}\n", "timeout must be"),
        ("name: d\nsource: s\nnodes: {a: {script: x.py, workers: 1.5}}\n", "workers must be"),
        ("name: d\nsource: s\nnodes: {a: {script: x.py, workers: true}}\n", "workers must be"),
        ("name: d\nsource: s\nnodes: {a: 1}\n", "must be a mapping"),
        ("name: d\nsource: s\nnodes:\n  a: {script: x.py}\n  a: {script: y.py}\n", "duplicate key"),
    ],
)
def test_parse_errors(tmp_path: Path, text: str, fragment: str) -> None:
    path = write(tmp_path / "dags" / "bad.yaml", text)
    with pytest.raises(DagError) as e:
        parse_dag(path, tmp_path)
    assert any(fragment in err for err in e.value.errors), e.value.errors
    assert all(err.startswith("dags/bad.yaml") for err in e.value.errors)


def _validate(repo: Path, nodes: str, sources: list[str] | None = None) -> list[str]:
    path = write(repo / "dags" / "v.yaml", f"name: v\nsource: proposals\nnodes:\n{nodes}")
    return validate_dag(parse_dag(path, repo), repo, sources)


def test_validate_ok(repo: Path) -> None:
    assert validate_dag(parse_dag(repo / "dags" / "demo.yaml", repo), repo, ["proposals"]) == []


@pytest.mark.parametrize(
    ("nodes", "fragment"),
    [
        (
            "  a: {script: nodes/parse.py, inputs: [b]}\n"
            "  b: {script: nodes/parse.py, inputs: [a]}\n",
            "cycle",
        ),
        ("  a: {script: nodes/parse.py, inputs: [a]}\n", "cycle"),
        ("  a: {script: nodes/parse.py, inputs: [ghost]}\n", "unknown input 'ghost'"),
        ("  a: {script: nodes/missing.py}\n", "not found"),
        ("  a: {script: /abs/x.py}\n", "repo-relative"),
        ("  a: {script: ../x.py}\n", "repo-relative"),
        ("  a: {script: nodes/neither.py}\n", "neither"),
        ("  a: {script: nodes/both.py}\n", "both"),
        ("  a: {script: nodes/parse.py, checks: nodes/checks/missing.py}\n", "not found"),
        ("  a: {script: nodes/parse.py, checks: nodes/chunk.py}\n", "validate"),
        ("  a: {script: nodes/parse.py, params: {when: 2024-01-01}}\n", "plain value"),
        ("  a: {script: nodes/parse.py, params: {x: .nan}}\n", "not finite"),
        ("  a: {script: nodes/parse.py, params: {x: {1: a}}}\n", "non-string key"),
        ("  a: {script: nodes/parse.py, timeout: 0}\n", "timeout must be > 0"),
        ("  a: {script: nodes/parse.py, workers: 0}\n", "workers must be >= 1"),
    ],
)
def test_validate_errors(repo: Path, nodes: str, fragment: str) -> None:
    write(repo / "nodes" / "neither.py", "x = 1\n")
    write(repo / "nodes" / "both.py", "def run(*a): ...\ndef run_corpus(*a): ...\n")
    errors = _validate(repo, nodes)
    assert any(fragment in e for e in errors), errors


def test_validate_unknown_source(repo: Path) -> None:
    nodes = "  a: {script: nodes/parse.py}\n"
    assert _validate(repo, nodes) == []
    assert _validate(repo, nodes, ["proposals"]) == []
    assert any("unknown source" in e for e in _validate(repo, nodes, ["other"]))


def test_cyclic_topo_order_raises(repo: Path) -> None:
    path = write(
        repo / "dags" / "v.yaml",
        "name: v\nsource: s\nnodes:\n"
        "  a: {script: x.py, inputs: [b]}\n  b: {script: x.py, inputs: [a]}\n",
    )
    with pytest.raises(DagError):
        parse_dag(path, repo).topo_order()


def test_load_dag_lists_every_problem(repo: Path) -> None:
    path = write(
        repo / "dags" / "v.yaml",
        "name: v\nsource: s\nnodes:\n  a: {script: nodes/missing.py, workers: 0}\n",
    )
    with pytest.raises(DagError) as e:
        load_dag(path, repo, ["proposals"])
    assert len(e.value.errors) == 3


def test_dag_paths_and_load_dags(repo: Path) -> None:
    write(repo / "dags" / "second.yml", DAG.replace("name: demo", "name: second"))
    write(repo / "dags" / "notes.txt", "ignored")
    assert [p.name for p in dag_paths(repo)] == ["demo.yaml", "second.yml"]
    dags = load_dags(repo, ["proposals"])
    assert sorted(dags) == ["demo", "second"]
    assert dags["second"].path == "dags/second.yml"


def test_load_dags_duplicate_name(repo: Path) -> None:
    write(repo / "dags" / "copy.yaml", DAG)
    with pytest.raises(DagError) as e:
        load_dags(repo)
    assert any("already declared" in err for err in e.value.errors)


def test_load_dags_invalid(repo: Path) -> None:
    write(
        repo / "dags" / "zbad.yaml",
        "name: bad\nsource: proposals\nnodes: {a: {script: nodes/x.py}}\n",
    )
    with pytest.raises(DagError) as e:
        load_dags(repo)
    assert e.value.errors[0].startswith("dags/zbad.yaml")


def test_dag_paths_without_dir(tmp_path: Path) -> None:
    assert dag_paths(tmp_path) == []
    assert load_dags(tmp_path) == {}
