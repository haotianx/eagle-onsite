"""Node states and promotion."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from ingest.config import Config
from ingest.spec import DagSpec
from ingest.store.catalog import Catalog, VersionRow

StateName: TypeAlias = Literal["fresh", "stale", "candidate", "running", "failed", "never_run"]


@dataclass(frozen=True)
class NodeState:
    node: str
    state: StateName
    """Precedence: running > failed (latest run of this node failed and nothing newer) >
    never_run > stale > candidate > fresh."""
    current: VersionRow | None
    candidates: tuple[VersionRow, ...]
    """Complete, promotable versions newer than current."""
    scratch: tuple[VersionRow, ...]
    fingerprint: str
    """From the working tree with current inputs."""
    dirty: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)
    """Why stale, e.g. "code or params changed", "input chunk is not current"."""


def node_states(catalog: Catalog, config: Config, dag: DagSpec) -> dict[str, NodeState]:
    """stale when the current version's node_fp differs from the working-tree fingerprint
    computed over current input versions, when it was built from input versions that aren't
    current, or when its snapshot isn't the source's latest."""
    raise NotImplementedError


def promotable(catalog: Catalog, version_id: int) -> tuple[bool, list[str]]:
    """Not promotable: incomplete, scratch, or already current. Returns (ok, reasons)."""
    raise NotImplementedError


def promote(catalog: Catalog, config: Config, version_id: int) -> list[str]:
    """Make the version current (per node; upstream untouched). For table versions also write
    publish.write_published. Returns warnings: inputs that aren't current, error/warning issue
    counts. Raises ValueError when not promotable."""
    raise NotImplementedError
