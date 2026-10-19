from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from ingest.fingerprint import (
    canonical_json,
    checks_fingerprint,
    dag_fingerprints,
    env_hash,
    node_fingerprint,
    params_hash,
    sha256_bytes,
    sha256_file,
)
from ingest.spec import DagSpec, NodeSpec


def test_sha256(tmp_path: Path) -> None:
    data = b"x" * (3 << 20) + b"tail"
    f = tmp_path / "f.bin"
    f.write_bytes(data)
    expected = hashlib.sha256(data).hexdigest()
    assert sha256_bytes(data) == expected
    assert sha256_file(f) == expected


def test_canonical_json() -> None:
    assert canonical_json({"b": 1, "a": [1, "é"]}) == '{"a":[1,"é"],"b":1}'
    assert params_hash({"a": 1, "b": 2}) == params_hash({"b": 2, "a": 1})
    assert params_hash({"a": 1}) != params_hash({"a": 1.5})


def test_env_hash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = env_hash(tmp_path)
    (tmp_path / "uv.lock").write_bytes(b"")
    assert env_hash(tmp_path) == missing
    (tmp_path / "uv.lock").write_bytes(b"lock v1")
    v1 = env_hash(tmp_path)
    assert v1 != missing
    assert env_hash(tmp_path) == v1
    monkeypatch.setattr("ingest.__version__", "999")
    assert env_hash(tmp_path) != v1
    monkeypatch.undo()
    fake_sys = SimpleNamespace(version_info=SimpleNamespace(major=3, minor=99))
    monkeypatch.setattr("ingest.fingerprint.sys", fake_sys)
    assert env_hash(tmp_path) != v1


def test_node_fingerprint() -> None:
    base = node_fingerprint("s", {"a": 1}, "e", {"x": "1", "y": "2"})
    assert base == node_fingerprint("s", {"a": 1}, "e", {"y": "2", "x": "1"})
    assert len(base) == 64
    assert base != node_fingerprint("t", {"a": 1}, "e", {"x": "1", "y": "2"})
    assert base != node_fingerprint("s", {"a": 2}, "e", {"x": "1", "y": "2"})
    assert base != node_fingerprint("s", {"a": 1}, "f", {"x": "1", "y": "2"})
    assert base != node_fingerprint("s", {"a": 1}, "e", {"x": "1", "y": "3"})
    assert base != node_fingerprint("s", {"a": 1}, "e", {"x": "1", "z": "2"})
    assert checks_fingerprint("c", {"a": 1}) != checks_fingerprint("c", {"a": 2})
    assert checks_fingerprint("c", {"a": 1}) != checks_fingerprint("d", {"a": 1})


def _dag(**overrides: NodeSpec) -> DagSpec:
    nodes = {
        "a": NodeSpec("a", "nodes/a.py", {"p": 1}),
        "b": NodeSpec("b", "nodes/b.py", {}, ("a",)),
        "c": NodeSpec("c", "nodes/b.py", {"q": 1}, ("b",)),
        "d": NodeSpec("d", "nodes/a.py", {"p": 1}),
    }
    nodes.update(overrides)
    return DagSpec(name="t", source="s", nodes=nodes, path="dags/t.yaml")


@pytest.fixture
def scripts_root(tmp_path: Path) -> Path:
    (tmp_path / "nodes").mkdir()
    (tmp_path / "nodes" / "a.py").write_text("def run(doc, inputs, params, ctx): ...\n")
    (tmp_path / "nodes" / "b.py").write_text("def run(doc, inputs, params, ctx): return 1\n")
    return tmp_path


def test_dag_fingerprints_chain(scripts_root: Path) -> None:
    fps = dag_fingerprints(_dag(), scripts_root, "env")
    assert list(fps) == ["a", "b", "c", "d"]
    a_hash = sha256_file(scripts_root / "nodes" / "a.py")
    assert fps["a"] == node_fingerprint(a_hash, {"p": 1}, "env", {})
    assert fps["a"] == fps["d"]
    b_hash = sha256_file(scripts_root / "nodes" / "b.py")
    assert fps["b"] == node_fingerprint(b_hash, {}, "env", {"a": fps["a"]})

    (scripts_root / "nodes" / "a.py").write_text("def run(doc, inputs, params, ctx): return 2\n")
    changed = dag_fingerprints(_dag(), scripts_root, "env")
    assert all(changed[n] != fps[n] for n in "abcd")


def test_dag_fingerprints_exclude_execution_settings(scripts_root: Path) -> None:
    fps = dag_fingerprints(_dag(), scripts_root, "env")
    tweaked = _dag(
        a=NodeSpec("a", "nodes/a.py", {"p": 1}, timeout=5, workers=1, checks="nodes/checks/a.py")
    )
    assert dag_fingerprints(tweaked, scripts_root, "env") == fps
    assert dag_fingerprints(_dag(), scripts_root, "env2") != fps


def test_dag_fingerprints_overrides(scripts_root: Path) -> None:
    fps = dag_fingerprints(_dag(), scripts_root, "env")
    over = dag_fingerprints(_dag(), scripts_root, "env", {"b": "older"})
    assert over["a"] == fps["a"]
    assert over["b"] == "older"
    c_hash = sha256_file(scripts_root / "nodes" / "b.py")
    assert over["c"] == node_fingerprint(c_hash, {"q": 1}, "env", {"b": "older"})
    assert over["c"] != fps["c"]
