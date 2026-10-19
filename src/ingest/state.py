"""Node states and promotion."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from ingest.config import Config
from ingest.engine.planner import dirty_nodes
from ingest.fingerprint import dag_fingerprints, env_hash, node_fingerprint, sha256_file
from ingest.spec import DagSpec
from ingest.store.catalog import Catalog, RunRow, VersionRow

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
    current, or when its snapshot isn't the source's latest. Staleness propagates: a node
    whose current input is stale is stale too ("input X is stale").

    never_run: no current and no candidate version. failed: the latest finished run that
    included the node failed without producing a version of it."""
    repo = config.repo_root
    env = env_hash(repo)
    working = dag_fingerprints(dag, repo, env)
    current = catalog.current_versions(dag.name)
    latest = catalog.latest_snapshot(dag.source)
    dirty = dirty_nodes(repo, dag)
    runs = catalog.list_runs(dag.name, limit=1000)
    script_hashes: dict[str, str] = {}

    def fp_with(name: str, input_fps: dict[str, str]) -> str:
        node = dag.nodes[name]
        if node.script not in script_hashes:
            script_hashes[node.script] = sha256_file(repo / node.script)
        return node_fingerprint(script_hashes[node.script], node.params, env, input_fps)

    out: dict[str, NodeState] = {}
    for name in dag.topo_order():
        node = dag.nodes[name]
        cur = current.get(name)
        fp = fp_with(name, {i: current[i].node_fp if i in current else working[i] for i in node.inputs})
        versions = catalog.list_versions(dag.name, name)
        candidates = tuple(
            v for v in versions if not v.scratch and v.seq is not None and (cur is None or v.seq > (cur.seq or 0))
        )
        scratch = tuple(v for v in versions if v.scratch)

        reasons: list[str] = []
        if cur is not None:
            built_from = {}
            for i in node.inputs:
                used = cur.input_versions.get(i)
                try:
                    built_from[i] = catalog.get_version(used).node_fp if used is not None else working[i]
                except KeyError:
                    built_from[i] = working[i]
                if i not in current or current[i].id != used:
                    reasons.append(f"input {i} is not current")
                elif out[i].reasons:
                    reasons.append(f"input {i} is stale")
            if fp_with(name, built_from) != cur.node_fp:
                reasons.insert(0, "code or params changed")
            if latest is not None and cur.snapshot_id != latest.id:
                reasons.append("snapshot is not the latest")

        if any(r.status == "running" and name in (r.request.get("nodes") or ()) for r in runs):
            state: StateName = "running"
        elif _last_run_failed(catalog, runs, name):
            state = "failed"
        elif cur is None and not candidates:
            state = "never_run"
        elif reasons:
            state = "stale"
        elif candidates:
            state = "candidate"
        else:
            state = "fresh"
        out[name] = NodeState(
            node=name,
            state=state,
            current=cur,
            candidates=candidates,
            scratch=scratch,
            fingerprint=fp,
            dirty=name in dirty,
            reasons=tuple(reasons),
        )
    return out


def _last_run_failed(catalog: Catalog, runs: list[RunRow], node: str) -> bool:
    for r in runs:  # newest first
        if r.status in ("queued", "running") or node not in (r.request.get("nodes") or ()):
            continue
        if r.status != "failed":
            return False
        return not any(v.node == node and v.status == "complete" for v in catalog.run_versions(r.id))
    return False


def promotable(catalog: Catalog, version_id: int) -> tuple[bool, list[str]]:
    """Not promotable: incomplete, scratch, or already current. Returns (ok, reasons)."""
    v = catalog.get_version(version_id)
    reasons = []
    if v.status != "complete":
        reasons.append(f"version is {v.status}")
    if v.scratch:
        reasons.append("scratch version (doc subset or working tree)")
    current = catalog.current_version(v.dag, v.node)
    if current is not None and current.id == v.id:
        reasons.append("already current")
    return not reasons, reasons


def promote(catalog: Catalog, config: Config, version_id: int) -> list[str]:
    """Make the version current (per node; upstream untouched). For table versions also write
    publish.write_published. Returns warnings: inputs that aren't current, error/warning issue
    counts. Raises ValueError when not promotable."""
    from ingest.publish import write_published
    from ingest.store.objects import ObjectStore

    ok, reasons = promotable(catalog, version_id)
    v = catalog.get_version(version_id)
    if not ok:
        raise ValueError(f"{v.label} is not promotable: {'; '.join(reasons)}")
    warnings = []
    for name, used in sorted(v.input_versions.items()):
        cur = catalog.current_version(v.dag, name)
        if cur is None or cur.id != used:
            built = catalog.get_version(used).label
            warnings.append(f"built from {built}, but the current {v.dag}/{name} is {cur.label if cur else 'none'}")
    issues = catalog.issues(version_id)
    for level in ("error", "warning"):
        n = sum(1 for i in issues if i.level == level)
        if n:
            warnings.append(f"{n} {level} issue(s)")
    catalog.set_current(v.dag, v.node, version_id)
    docs = [d for d in catalog.version_docs(version_id) if d.status == "ok"]
    if docs and all(d.kind == "parquet" for d in docs):
        write_published(catalog, ObjectStore(config.objects_dir), version_id, config.published_dir)
    return warnings
