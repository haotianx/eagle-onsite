"""DAG YAML parsing and validation."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from ingest.scripts import inspect_checks, inspect_script

DAGS_DIR = "dags"
DEFAULT_TIMEOUT_S = 300.0
DEFAULT_WORKERS = 4


class DagError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class NodeSpec:
    name: str
    script: str
    """Repo-relative POSIX path."""
    params: Mapping[str, Any] = field(default_factory=dict)
    inputs: tuple[str, ...] = ()
    checks: str | None = None
    """Repo-relative path of an optional checks file."""
    timeout: float = DEFAULT_TIMEOUT_S
    workers: int = DEFAULT_WORKERS


@dataclass(frozen=True)
class DagSpec:
    name: str
    source: str
    nodes: Mapping[str, NodeSpec]
    path: str
    """Repo-relative path of the YAML file."""

    def topo_order(self) -> list[str]:
        """Nodes in dependency order; ties broken by YAML order."""
        order, rest = _kahn(self.nodes)
        if rest:
            raise DagError([f"{self.path}: cycle among nodes {', '.join(rest)}"])
        return order

    def roots(self) -> list[str]:
        return [n for n in self.topo_order() if not self.nodes[n].inputs]

    def upstream(self, node: str) -> list[str]:
        """Transitive inputs of `node`, in topo order, excluding `node`."""
        seen = self._closure(node, lambda n: self.nodes[n].inputs)
        return [n for n in self.topo_order() if n in seen]

    def downstream(self, node: str) -> list[str]:
        """Transitive dependents of `node`, in topo order, excluding `node`."""
        dependents: dict[str, list[str]] = {n: [] for n in self.nodes}
        for n, spec in self.nodes.items():
            for i in spec.inputs:
                if i in dependents:
                    dependents[i].append(n)
        seen = self._closure(node, lambda n: dependents[n])
        return [n for n in self.topo_order() if n in seen]

    def node_files(self, node: str) -> list[str]:
        """Repo-relative files that define `node`: the DAG YAML, its script, its checks file."""
        spec = self.nodes[node]
        return [self.path, spec.script] + ([spec.checks] if spec.checks else [])

    def _closure(self, node: str, edges: Any) -> set[str]:
        if node not in self.nodes:
            raise KeyError(node)
        seen: set[str] = set()
        stack = [node]
        while stack:
            for nxt in edges(stack.pop()):
                if nxt in self.nodes and nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        seen.discard(node)
        return seen


def _kahn(nodes: Mapping[str, NodeSpec]) -> tuple[list[str], list[str]]:
    """Topo order ignoring unknown inputs, earliest YAML position first; also the nodes left
    over because they are on or behind a cycle."""
    done: set[str] = set()
    order: list[str] = []
    pending = list(nodes)
    progress = True
    while pending and progress:
        progress = False
        for n in pending:
            if all(i in done or i not in nodes for i in nodes[n].inputs):
                order.append(n)
                done.add(n)
                pending.remove(n)
                progress = True
                break
    return order, pending


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    out: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        try:
            duplicate = key in out
        except TypeError:
            raise yaml.constructor.ConstructorError(
                None, None, f"unhashable key {key!r}", key_node.start_mark
            ) from None
        if duplicate:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key {key!r}", key_node.start_mark
            )
        out[key] = loader.construct_object(value_node, deep=True)
    return out


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)

_DAG_KEYS = {"name", "source", "nodes"}
_NODE_KEYS = {"script", "params", "inputs", "checks", "timeout", "workers"}


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _rel_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def dag_paths(root: Path) -> list[Path]:
    """All dags/*.yaml (and *.yml) under `root`, sorted."""
    d = root / DAGS_DIR
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.suffix in (".yaml", ".yml") and p.is_file())


def parse_dag(path: Path, root: Path) -> DagSpec:
    """Parse one DAG file. `root` is the directory script paths are relative to (the repo, or a
    materialized HEAD tree). Raises DagError on malformed YAML or structure (unknown keys, wrong
    types, missing name/source/nodes, duplicate nodes)."""
    rel = _rel_path(root, path)
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except OSError as e:
        raise DagError([f"{rel}: cannot read: {e.strerror or e}"]) from e
    except yaml.YAMLError as e:
        raise DagError([f"{rel}: invalid YAML: {e}"]) from e

    if not isinstance(raw, dict):
        raise DagError([f"{rel}: must be a mapping with name, source and nodes"])
    errors: list[str] = []
    unknown = set(raw) - _DAG_KEYS
    if unknown:
        errors.append(f"{rel}: unknown keys {sorted(map(str, unknown))}")
    for key in ("name", "source"):
        if key not in raw:
            errors.append(f"{rel}: missing {key}")
        elif not isinstance(raw[key], str) or not raw[key]:
            errors.append(f"{rel}: {key} must be a non-empty string")
    raw_nodes = raw.get("nodes")
    nodes: dict[str, NodeSpec] = {}
    if "nodes" not in raw:
        errors.append(f"{rel}: missing nodes")
    elif not isinstance(raw_nodes, dict) or not raw_nodes:
        errors.append(f"{rel}: nodes must be a non-empty mapping")
    else:
        for name, body in raw_nodes.items():
            node = _parse_node(rel, name, body, errors)
            if node is not None:
                nodes[name] = node
    if errors:
        raise DagError(errors)
    return DagSpec(name=raw["name"], source=raw["source"], nodes=nodes, path=rel)


def _parse_node(rel: str, name: Any, body: Any, errors: list[str]) -> NodeSpec | None:
    if not isinstance(name, str) or not name:
        errors.append(f"{rel}: node name {name!r} must be a non-empty string")
        return None
    where = f"{rel}: node {name!r}"
    if not isinstance(body, dict):
        errors.append(f"{where}: must be a mapping")
        return None
    n = len(errors)
    unknown = set(body) - _NODE_KEYS
    if unknown:
        errors.append(f"{where}: unknown keys {sorted(map(str, unknown))}")
    script = body.get("script")
    if "script" not in body:
        errors.append(f"{where}: missing script")
    elif not isinstance(script, str) or not script:
        errors.append(f"{where}: script must be a non-empty string")
    params = body.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        errors.append(f"{where}: params must be a mapping")
    inputs = body.get("inputs", [])
    if inputs is None:
        inputs = []
    if not isinstance(inputs, list) or not all(isinstance(i, str) for i in inputs):
        errors.append(f"{where}: inputs must be a list of node names")
    elif len(set(inputs)) != len(inputs):
        errors.append(f"{where}: duplicate inputs")
    checks = body.get("checks")
    if checks is not None and (not isinstance(checks, str) or not checks):
        errors.append(f"{where}: checks must be a non-empty string")
    timeout = body.get("timeout", DEFAULT_TIMEOUT_S)
    if not _is_number(timeout):
        errors.append(f"{where}: timeout must be a number")
    workers = body.get("workers", DEFAULT_WORKERS)
    if not isinstance(workers, int) or isinstance(workers, bool):
        errors.append(f"{where}: workers must be an integer")
    if len(errors) > n:
        return None
    return NodeSpec(
        name=name,
        script=script,
        params=params,
        inputs=tuple(inputs),
        checks=checks,
        timeout=float(timeout),
        workers=workers,
    )


def _plain_value_error(value: Any, where: str) -> str | None:
    if value is None or isinstance(value, (str, bool, int)):
        return None
    if isinstance(value, float):
        return None if math.isfinite(value) else f"{where} is not finite"
    if isinstance(value, list):
        for i, v in enumerate(value):
            if err := _plain_value_error(v, f"{where}[{i}]"):
                return err
        return None
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                return f"{where} has non-string key {k!r}"
            if err := _plain_value_error(v, f"{where}.{k}"):
                return err
        return None
    return f"{where} is a {type(value).__name__}, not a plain value"


def _path_error(root: Path, value: str, what: str) -> str | None:
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts or "\\" in value:
        return f"{what} {value!r} must be a repo-relative POSIX path"
    if not (root / value).is_file():
        return f"{what} {value!r} not found"
    return None


def validate_dag(dag: DagSpec, root: Path, sources: Iterable[str] | None = None) -> list[str]:
    """Semantic checks; returns error strings (empty = valid): cycles, unknown inputs, missing
    script or checks files, scripts that define neither or both of run/run_corpus (checked
    statically, see scripts.inspect_script), checks files without validate(), params that aren't
    plain YAML values, timeout <= 0, workers < 1, unknown source (when `sources` is given)."""
    errors: list[str] = []
    if sources is not None:
        known = sorted(sources)
        if dag.source not in known:
            errors.append(f"{dag.path}: unknown source {dag.source!r} (known: {', '.join(known)})")
    for name, node in dag.nodes.items():
        where = f"{dag.path}: node {name!r}"
        for i in node.inputs:
            if i not in dag.nodes:
                errors.append(f"{where}: unknown input {i!r}")
        if err := _path_error(root, node.script, "script"):
            errors.append(f"{where}: {err}")
        else:
            errors.extend(f"{where}: {e}" for e in inspect_script(root / node.script))
        if node.checks is not None:
            if err := _path_error(root, node.checks, "checks file"):
                errors.append(f"{where}: {err}")
            else:
                errors.extend(f"{where}: {e}" for e in inspect_checks(root / node.checks))
        if err := _plain_value_error(dict(node.params), "params"):
            errors.append(f"{where}: {err}")
        if not node.timeout > 0:
            errors.append(f"{where}: timeout must be > 0")
        if node.workers < 1:
            errors.append(f"{where}: workers must be >= 1")
    _, cyclic = _kahn(dag.nodes)
    if cyclic:
        errors.append(f"{dag.path}: cycle among nodes {', '.join(cyclic)}")
    return errors


def load_dag(path: Path, root: Path, sources: Iterable[str] | None = None) -> DagSpec:
    """parse_dag + validate_dag; raises DagError listing every problem."""
    dag = parse_dag(path, root)
    errors = validate_dag(dag, root, sources)
    if errors:
        raise DagError(errors)
    return dag


def load_dags(root: Path, sources: Iterable[str] | None = None) -> dict[str, DagSpec]:
    """Load every DAG under `root`, keyed by name. Raises DagError on any invalid DAG or on two
    files declaring the same name."""
    sources = None if sources is None else list(sources)
    dags: dict[str, DagSpec] = {}
    errors: list[str] = []
    for path in dag_paths(root):
        try:
            dag = load_dag(path, root, sources)
        except DagError as e:
            errors.extend(e.errors)
            continue
        if dag.name in dags:
            errors.append(
                f"{dag.path}: DAG name {dag.name!r} already declared in {dags[dag.name].path}"
            )
            continue
        dags[dag.name] = dag
    if errors:
        raise DagError(errors)
    return dags
