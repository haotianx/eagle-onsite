"""In-memory stand-ins for Phase 1 engine stubs, so the engineer UI runs against seeded data.

install(config) replaces each of these module attributes only while it still raises
NotImplementedError (real implementations win once they land):
    ingest.state.node_states, promotable, promote
    ingest.engine.planner.selected_nodes, plan_run
    ingest.engine.executor.submit, cancel

The fake executor plans with the (real or fake) planner, then a background thread emits
plausible run events over about ten seconds without running scripts or creating versions.
"""

from __future__ import annotations

import ast
import inspect
import random
import textwrap
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ingest import gitinfo, obs, publish, state
from ingest.config import Config
from ingest.engine import executor, planner
from ingest.engine.planner import InputRef, NodePlan, PlanError, RunPlan, RunRequest, Selection
from ingest.fingerprint import dag_fingerprints, env_hash, node_fingerprint, sha256_file
from ingest.spec import DagSpec, load_dags
from ingest.state import NodeState
from ingest.store.catalog import Catalog, VersionRow
from ingest.store.objects import ObjectStore

RUN_SECONDS = 10.0


def is_stub(fn: Callable[..., Any]) -> bool:
    """True when the function body is only a docstring and `raise NotImplementedError`."""
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    except (OSError, TypeError, SyntaxError):
        return False
    if not tree.body or not isinstance(tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    body = tree.body[0].body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    if len(body) != 1 or not isinstance(body[0], ast.Raise):
        return False
    exc = body[0].exc
    name = exc.func if isinstance(exc, ast.Call) else exc
    return isinstance(name, ast.Name) and name.id == "NotImplementedError"


def install(config: Config) -> list[str]:
    """Patch the stubs that still raise NotImplementedError; returns the names replaced."""
    replacements: list[tuple[Any, str, Callable[..., Any]]] = [
        (state, "node_states", node_states),
        (state, "promotable", promotable),
        (state, "promote", promote),
        (planner, "selected_nodes", selected_nodes),
        (planner, "plan_run", plan_run),
        (executor, "submit", submit),
        (executor, "cancel", cancel),
    ]
    done = []
    for module, name, fake in replacements:
        if is_stub(getattr(module, name)):
            setattr(module, name, fake)
            done.append(f"{module.__name__}.{name}")
    return done


# ingest.state


def _working_fps(config: Config, dag: DagSpec) -> tuple[dict[str, str], str]:
    env = env_hash(config.repo_root)
    return dag_fingerprints(dag, config.repo_root, env), env


def _run_nodes(run: Any) -> list[str]:
    nodes = run.request.get("nodes")
    if isinstance(nodes, list):
        return nodes
    target = run.request.get("target")
    return [target] if target else []


def node_states(catalog: Catalog, config: Config, dag: DagSpec) -> dict[str, NodeState]:
    """stale when the current version's node_fp differs from the working-tree fingerprint
    computed over current input versions, when it was built from input versions that aren't
    current, or when its snapshot isn't the source's latest."""
    wt, env = _working_fps(config, dag)
    current = catalog.current_versions(dag.name)
    latest = catalog.latest_snapshot(dag.source)
    dirty = gitinfo.dirty_paths(config.repo_root)
    active = {n for r in catalog.active_runs(dag.name) for n in _run_nodes(r)}
    runs = catalog.list_runs(dag.name, limit=500)
    out: dict[str, NodeState] = {}
    for name in dag.topo_order():
        spec = dag.nodes[name]
        cur = current.get(name)
        inputs = {i: current[i].node_fp if i in current else wt[i] for i in spec.inputs}
        fp = node_fingerprint(sha256_file(config.repo_root / spec.script), spec.params, env, inputs)
        versions = catalog.list_versions(dag.name, name)
        scratch = tuple(v for v in versions if v.scratch)
        candidates = tuple(
            v for v in versions if not v.scratch and (cur is None or (v.seq or 0) > (cur.seq or 0))
        )
        reasons: list[str] = []
        if cur is not None:
            if cur.node_fp != fp:
                reasons.append("code or params changed")
            for i, vid in cur.input_versions.items():
                if i not in current or current[i].id != vid:
                    reasons.append(f"input {i} is not current")
            if latest is not None and cur.snapshot_id != latest.id:
                reasons.append("snapshot is not the latest")
        last = next((r for r in runs if name in _run_nodes(r) and r.status != "queued"), None)
        if name in active:
            st = "running"
        elif last is not None and last.status == "failed":
            st = "failed"
        elif cur is None:
            st = "never_run"
        elif reasons:
            st = "stale"
        elif candidates:
            st = "candidate"
        else:
            st = "fresh"
        out[name] = NodeState(
            node=name,
            state=st,  # type: ignore[arg-type]
            current=cur,
            candidates=candidates,
            scratch=scratch,
            fingerprint=fp,
            dirty=any(f in dirty for f in dag.node_files(name)),
            reasons=tuple(reasons),
        )
    return out


def promotable(catalog: Catalog, version_id: int) -> tuple[bool, list[str]]:
    """Not promotable: incomplete, scratch, or already current. Returns (ok, reasons)."""
    v = catalog.get_version(version_id)
    reasons = []
    if v.status != "complete":
        reasons.append(f"version is {v.status}")
    if v.scratch:
        reasons.append("scratch version")
    cur = catalog.current_version(v.dag, v.node)
    if cur is not None and cur.id == v.id:
        reasons.append("already current")
    return not reasons, reasons


def promote(catalog: Catalog, config: Config, version_id: int) -> list[str]:
    """Make the version current (per node; upstream untouched). For table versions also write
    publish.write_published. Returns warnings: inputs that aren't current, error/warning issue
    counts. Raises ValueError when not promotable."""
    ok, reasons = promotable(catalog, version_id)
    if not ok:
        raise ValueError("; ".join(reasons))
    v = catalog.get_version(version_id)
    catalog.set_current(v.dag, v.node, v.id)
    if any(d.kind == "parquet" for d in catalog.version_docs(v.id) if d.status == "ok"):
        publish.write_published(catalog, ObjectStore(config.objects_dir), v.id, config.published_dir)
    warnings = []
    current = catalog.current_versions(v.dag)
    for i, vid in v.input_versions.items():
        if i not in current or current[i].id != vid:
            warnings.append(f"input {i} is not current")
    issues = catalog.issues(v.id)
    for level in ("error", "warning"):
        n = sum(1 for i in issues if i.level == level)
        if n:
            warnings.append(f"{n} {level}{'s' if n != 1 else ''}")
    return warnings


# ingest.engine.planner


def selected_nodes(dag: DagSpec, selection: Selection, target: str | None) -> list[str]:
    if selection == "all":
        return dag.topo_order()
    if target is None or target not in dag.nodes:
        raise PlanError(f"unknown node {target!r} in DAG {dag.name!r}")
    if selection == "node":
        return [target]
    if selection == "downstream":
        return [target, *dag.downstream(target)]
    if selection == "upstream":
        return [*dag.upstream(target), target]
    raise PlanError(f"unknown selection {selection!r}")


def plan_run(catalog: Catalog, config: Config, request: RunRequest, *, code_root: Path | None = None) -> RunPlan:
    """Resolve DAG (from the working tree, or `code_root` holding a materialized HEAD tree for
    committed runs), snapshot, docs, input versions and fingerprints, and count cached docs.
    Raises PlanError for unknown DAG/node/version, a missing snapshot, an input with no current
    version and no override, or code == "committed" with no commits."""
    root = code_root or config.repo_root
    dag = load_dags(root, config.sources).get(request.dag)
    if dag is None:
        raise PlanError(f"unknown DAG {request.dag!r}")
    nodes = planner.selected_nodes(dag, request.selection, request.target)

    if request.snapshot_id is not None:
        try:
            snapshot = catalog.get_snapshot(request.snapshot_id)
        except KeyError:
            raise PlanError(f"snapshot {request.snapshot_id} not found") from None
    else:
        snapshot = catalog.latest_snapshot(dag.source)
        if snapshot is None:
            raise PlanError(f"source {dag.source!r} has no snapshot; sync it first")
    all_ids = [d.doc_id for d in catalog.snapshot_docs(snapshot.id)]
    if request.docs == "all":
        doc_ids = all_ids
    elif isinstance(request.docs, int):
        doc_ids = sorted(random.Random(request.sample_seed).sample(all_ids, min(request.docs, len(all_ids))))
    else:
        unknown = sorted(set(request.docs) - set(all_ids))
        if unknown:
            raise PlanError(f"docs not in snapshot {snapshot.id}: {', '.join(unknown)}")
        doc_ids = list(dict.fromkeys(request.docs))

    files = [f for n in nodes for f in dag.node_files(n)]
    dirty = gitinfo.is_dirty(config.repo_root, files)
    code = request.code if request.code != "auto" else ("working" if dirty else "committed")
    commit = gitinfo.head_commit(config.repo_root)
    if code == "committed" and commit is None:
        raise PlanError("no commits to run committed code from")

    resolved: dict[str, VersionRow] = {}
    for n in nodes:
        for i in dag.nodes[n].inputs:
            if i in nodes or i in resolved:
                continue
            vid = request.input_versions.get(i)
            if vid is not None:
                try:
                    v = catalog.get_version(vid)
                except KeyError:
                    raise PlanError(f"version {vid} not found") from None
                if (v.dag, v.node) != (dag.name, i):
                    raise PlanError(f"version {vid} is not a version of {dag.name}/{i}")
            else:
                v = catalog.current_version(dag.name, i)
                if v is None:
                    raise PlanError(f"input {i} has no current version")
            resolved[i] = v

    env = env_hash(root)
    fps = dag_fingerprints(dag, root, env, {i: v.node_fp for i, v in resolved.items()})
    plans = []
    for n in nodes:
        spec = dag.nodes[n]
        inputs = {
            i: InputRef(i, None, fps[i]) if i in nodes else InputRef(i, resolved[i].id, resolved[i].node_fp)
            for i in spec.inputs
        }
        blocked: set[str] = set()
        for i, v in resolved.items():
            if i in spec.inputs:
                ok = {d.doc_id for d in catalog.version_docs(v.id) if d.status == "ok"}
                blocked |= set(doc_ids) - ok
        cached = set() if request.recompute == "force" else set(catalog.cache_get_many(fps[n], doc_ids)) - blocked
        plans.append(
            NodePlan(
                node=n,
                script=spec.script,
                fingerprint=fps[n],
                artifact_type=_artifact_type(root / spec.script),
                inputs=inputs,
                docs_total=len(doc_ids),
                docs_cached=len(cached),
                docs_to_process=len(doc_ids) - len(cached) - len(blocked),
                docs_blocked=len(blocked),
            )
        )
    reasons = []
    if len(doc_ids) != len(all_ids):
        reasons.append("doc subset")
    if code == "working":
        reasons.append("working tree")
    return RunPlan(
        request=request,
        dag=dag,
        code=code,  # type: ignore[arg-type]
        commit=commit,
        dirty=dirty,
        snapshot_id=snapshot.id,
        doc_ids=tuple(doc_ids),
        scratch=bool(reasons),
        scratch_reasons=tuple(reasons),
        nodes=tuple(plans),
        env=env,
    )


def _artifact_type(path: Path) -> str | None:
    """ARTIFACT_TYPE read statically, so planning doesn't import ML libraries."""
    tree = ast.parse(path.read_bytes())
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "ARTIFACT_TYPE" for t in stmt.targets):
            if isinstance(stmt.value, ast.Constant):
                return stmt.value.value
    return None


# ingest.engine.executor


def submit(config: Config, request: RunRequest, *, online: bool = False) -> int:
    """Create a queued run and start a detached subprocess (`python -m ingest.engine.executor
    <run_id>`) that plans and executes it; returns the run id immediately (API)."""
    catalog = Catalog(config.catalog_path)
    try:
        plan = planner.plan_run(catalog, config, request)
        body = {**asdict(request), "input_versions": dict(request.input_versions), "nodes": [p.node for p in plan.nodes]}
        if not isinstance(body["docs"], (str, int)):
            body["docs"] = list(body["docs"])
        run_id = catalog.create_run(request.dag, body, scratch=plan.scratch, commit=plan.commit, dirty=plan.dirty)
        catalog.add_event(run_id, "run_queued", {})
    finally:
        catalog.close()
    threading.Thread(target=_simulate, args=(config, run_id, plan), name=f"fake-run-{run_id}", daemon=True).start()
    return run_id


def cancel(catalog: Catalog, run_id: int) -> None:
    """Request cancellation; the executor stops dispatching, kills workers and abandons
    building versions."""
    catalog.request_cancel(run_id)


def _simulate(config: Config, run_id: int, plan: RunPlan) -> None:
    catalog = Catalog(config.catalog_path)
    log = obs.RunLog(config.run_dir(run_id), run_id=run_id, dag=plan.dag.name)

    def event(kind: str, payload: dict[str, Any], **tags: Any) -> None:
        catalog.add_event(run_id, kind, payload)
        log.event(kind, message=kind.replace("_", " "), **{**payload, **tags})

    def cancelled() -> bool:
        return catalog.get_run(run_id).cancel_requested

    try:
        time.sleep(0.5)
        if cancelled():
            event("run_finished", {"status": "cancelled"})
            catalog.set_run_status(run_id, "cancelled")
            return
        catalog.set_run_status(run_id, "running")
        nodes = [p.node for p in plan.nodes]
        event("run_started", {"docs": len(plan.doc_ids), "nodes": nodes, "snapshot_id": plan.snapshot_id,
                              "scratch": plan.scratch, "code": plan.code})
        steps = max(1, sum(p.docs_to_process for p in plan.nodes))
        delay = RUN_SECONDS / steps
        for p in plan.nodes:
            event("node_started", {"node": p.node, "version_id": None}, node=p.node)
            start = time.monotonic()
            cached = (
                set()
                if plan.request.recompute == "force"
                else set(catalog.cache_get_many(p.fingerprint, list(plan.doc_ids)))
            )
            done = 0
            for doc_id in plan.doc_ids:
                if cancelled():
                    event("run_finished", {"status": "cancelled"})
                    catalog.set_run_status(run_id, "cancelled")
                    return
                if doc_id in cached:
                    event("doc_cached", {"node": p.node, "doc_id": doc_id}, node=p.node, doc_id=doc_id)
                    continue
                time.sleep(delay)
                done += 1
                event("doc_finished", {"node": p.node, "doc_id": doc_id, "duration_s": round(delay, 3)},
                      node=p.node, doc_id=doc_id)
            wall = time.monotonic() - start
            event("node_finished", {"node": p.node, "version_id": None, "version": None, "processed": done,
                                    "cached": len(cached & set(plan.doc_ids)), "ok": len(plan.doc_ids), "error": 0,
                                    "skipped": 0, "wall_s": round(wall, 3)}, node=p.node)
            catalog.add_metrics(run_id, [(p.node, None, "wall_s", wall), (p.node, None, "docs_processed", done),
                                         (p.node, None, "docs_cached", len(plan.doc_ids) - done)])
        event("run_finished", {"status": "succeeded"})
        catalog.set_run_status(run_id, "succeeded")
    except Exception as e:
        catalog.set_run_status(run_id, "failed", f"{type(e).__name__}: {e}")
    finally:
        log.close()
        catalog.close()
