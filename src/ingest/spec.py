"""DAG YAML parsing and validation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
        raise NotImplementedError

    def roots(self) -> list[str]:
        raise NotImplementedError

    def upstream(self, node: str) -> list[str]:
        """Transitive inputs of `node`, in topo order, excluding `node`."""
        raise NotImplementedError

    def downstream(self, node: str) -> list[str]:
        """Transitive dependents of `node`, in topo order, excluding `node`."""
        raise NotImplementedError

    def node_files(self, node: str) -> list[str]:
        """Repo-relative files that define `node`: the DAG YAML, its script, its checks file."""
        raise NotImplementedError


def dag_paths(root: Path) -> list[Path]:
    """All dags/*.yaml (and *.yml) under `root`, sorted."""
    raise NotImplementedError


def parse_dag(path: Path, root: Path) -> DagSpec:
    """Parse one DAG file. `root` is the directory script paths are relative to (the repo, or a
    materialized HEAD tree). Raises DagError on malformed YAML or structure (unknown keys, wrong
    types, missing name/source/nodes, duplicate nodes)."""
    raise NotImplementedError


def validate_dag(dag: DagSpec, root: Path, sources: Iterable[str] | None = None) -> list[str]:
    """Semantic checks; returns error strings (empty = valid): cycles, unknown inputs, missing
    script or checks files, scripts that define neither or both of run/run_corpus (checked
    statically, see scripts.inspect_script), checks files without validate(), params that aren't
    plain YAML values, timeout <= 0, workers < 1, unknown source (when `sources` is given)."""
    raise NotImplementedError


def load_dag(path: Path, root: Path, sources: Iterable[str] | None = None) -> DagSpec:
    """parse_dag + validate_dag; raises DagError listing every problem."""
    raise NotImplementedError


def load_dags(root: Path, sources: Iterable[str] | None = None) -> dict[str, DagSpec]:
    """Load every DAG under `root`, keyed by name. Raises DagError on any invalid DAG or on two
    files declaring the same name."""
    raise NotImplementedError
