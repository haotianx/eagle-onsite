"""Turn a run request into a plan: which nodes, which docs, what is cached."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypeAlias

from ingest.config import Config
from ingest.spec import DagSpec
from ingest.store.catalog import Catalog

Selection: TypeAlias = Literal["node", "downstream", "upstream", "all"]


@dataclass(frozen=True)
class RunRequest:
    dag: str
    target: str | None = None
    """Node the selection is relative to; required unless selection == "all"."""
    selection: Selection = "all"
    snapshot_id: int | None = None
    """None → latest snapshot of the DAG's source."""
    input_versions: Mapping[str, int] = field(default_factory=dict)
    """Node name → version id to read instead of that node's current version. Applies to nodes
    outside the selection that a selected node reads."""
    recompute: Literal["reuse", "force"] = "reuse"
    docs: Literal["all"] | int | Sequence[str] = "all"
    """"all", a sample size, or explicit doc ids (any subset makes the run scratch)."""
    code: Literal["auto", "committed", "working"] = "auto"
    """auto → "working" if any selected node's files are dirty, else "committed"."""
    sample_seed: int = 0


@dataclass(frozen=True)
class InputRef:
    node: str
    version_id: int | None
    """None when the input is produced earlier in the same run."""
    fingerprint: str


@dataclass(frozen=True)
class NodePlan:
    node: str
    script: str
    fingerprint: str
    artifact_type: str | None
    inputs: Mapping[str, InputRef]
    docs_total: int
    docs_cached: int
    """Docs with a cache entry for (fingerprint, doc) — 0 when recompute == "force"."""
    docs_to_process: int
    docs_blocked: int
    """Docs that failed or are missing in an input version read from outside the run."""


@dataclass(frozen=True)
class RunPlan:
    request: RunRequest
    dag: DagSpec
    code: Literal["committed", "working"]
    commit: str | None
    dirty: bool
    """Whether the selected nodes' files differ from HEAD."""
    snapshot_id: int
    doc_ids: tuple[str, ...]
    scratch: bool
    scratch_reasons: tuple[str, ...]
    """e.g. "doc subset", "working tree"."""
    nodes: tuple[NodePlan, ...]
    """Selected nodes in topo order."""
    env: str


class PlanError(Exception):
    pass


def selected_nodes(dag: DagSpec, selection: Selection, target: str | None) -> list[str]:
    raise NotImplementedError


def plan_run(catalog: Catalog, config: Config, request: RunRequest, *, code_root: Path | None = None) -> RunPlan:
    """Resolve DAG (from the working tree, or `code_root` holding a materialized HEAD tree for
    committed runs), snapshot, docs, input versions and fingerprints, and count cached docs.
    Raises PlanError for unknown DAG/node/version, a missing snapshot, an input with no current
    version and no override, or code == "committed" with no commits."""
    raise NotImplementedError
