"""DAGs, DAG detail with node states, a node's versions."""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter

from ingest import gitinfo, state
from ingest.api.deps import CatalogDep, ConfigDep, not_found
from ingest.api.models import DagDetail, DagSummary, NodeInfo, VersionSummary
from ingest.api.views import (
    CurrentIds,
    DagFile,
    artifact_type,
    dag_files,
    find_dag,
    node_dirty,
    run_summary,
    version_summary,
)
from ingest.config import Config
from ingest.store.catalog import Catalog

router = APIRouter(tags=["dags"])


def _dirty(f: DagFile, dirty: set[str]) -> bool:
    if f.rel in dirty:
        return True
    return f.dag is not None and any(node_dirty(f.dag, n, dirty) for n in f.dag.nodes)


def _states(catalog: Catalog, config: Config, f: DagFile) -> dict[str, state.NodeState]:
    """Node states of a valid DAG; none for an invalid one."""
    if f.dag is None or f.errors:
        return {}
    return state.node_states(catalog, config, f.dag)


@router.get("/dags")
def list_dags(config: ConfigDep, catalog: CatalogDep) -> list[DagSummary]:
    dirty = gitinfo.dirty_paths(config.repo_root)
    out = []
    for f in dag_files(config):
        states = _states(catalog, config, f)
        runs = catalog.list_runs(f.name, limit=1) if f.dag is not None else []
        out.append(
            DagSummary(
                name=f.name,
                source=f.dag.source if f.dag else None,
                path=f.rel,
                dirty=_dirty(f, dirty),
                errors=f.errors,
                node_count=len(f.dag.nodes) if f.dag else 0,
                states=dict(Counter(s.state for s in states.values())),
                last_run=run_summary(catalog, runs[0]) if runs else None,
            )
        )
    return out


@router.get("/dags/{dag}")
def get_dag(dag: str, config: ConfigDep, catalog: CatalogDep) -> DagDetail:
    """Nodes are listed for valid DAGs only; an invalid one shows its errors."""
    f = find_dag(config, dag)
    if f is None:
        raise not_found(f"DAG {dag!r} not found")
    dirty = gitinfo.dirty_paths(config.repo_root)
    states = _states(catalog, config, f)
    active = catalog.active_runs(f.name) if states else []
    current = CurrentIds(catalog)
    nodes = []
    for name in f.dag.topo_order() if states else []:
        spec, s = f.dag.nodes[name], states[name]
        stats = s.current.stats if s.current else None
        nodes.append(
            NodeInfo(
                name=name,
                script=spec.script,
                params=dict(spec.params),
                inputs=list(spec.inputs),
                checks=spec.checks,
                timeout=spec.timeout,
                workers=spec.workers,
                artifact_type=artifact_type(config.repo_root / spec.script),
                state=s.state,
                reasons=list(s.reasons),
                dirty=node_dirty(f.dag, name, dirty),
                fingerprint=s.fingerprint,
                current=version_summary(s.current, current) if s.current else None,
                candidates=[version_summary(v, current) for v in s.candidates],
                scratch=[version_summary(v, current) for v in s.scratch],
                doc_count=int(stats.get("docs") or 0) if stats is not None else None,
                failure_count=int(stats.get("error") or 0) if stats is not None else None,
                active_run_id=next((r.id for r in active if name in (r.request.get("nodes") or [])), None),
            )
        )
    return DagDetail(
        name=f.name,
        source=f.dag.source if f.dag else None,
        path=f.rel,
        dirty=_dirty(f, dirty),
        errors=f.errors,
        nodes=nodes,
    )


@router.get("/dags/{dag}/nodes/{node}/versions")
def node_versions(dag: str, node: str, config: ConfigDep, catalog: CatalogDep) -> list[VersionSummary]:
    """Complete versions (incl. scratch), newest first."""
    versions = catalog.list_versions(dag, node)
    if not versions:
        f = find_dag(config, dag)
        if f is None or f.dag is None or node not in f.dag.nodes:
            raise not_found(f"Node {dag}/{node} not found")
    current = CurrentIds(catalog)
    return [version_summary(v, current) for v in versions]
