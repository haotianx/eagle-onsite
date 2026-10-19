"""Execute a run plan: worker processes, caching, validation, versions, logs and metrics.

Behaviour (see docs/Ingestion-Design.md §7, §8, §10, §11):
- Committed-code runs execute scripts from a temporary HEAD tree (gitinfo.materialize_head);
  working-tree runs from the repo.
- Per selected node in topo order: create a 'building' version; for each doc, reuse the cache
  entry for (node fingerprint, doc) unless recompute == "force"; skip docs that failed upstream
  (status 'skipped'); otherwise send the doc to a worker.
- Workers (engine/worker.py) are spawn-start processes, `NodeSpec.workers` per node, each calling
  setup() once and run() per doc inside obs.capture and obs.PeakRss. The parent enforces the
  per-doc timeout and replaces a worker that times out or dies; only that doc fails.
- Results come back to the parent, which alone writes objects, the cache index, catalog rows,
  log lines and metrics. Hard, quality and script-declared checks run on each new record
  (validation.py); a hard-check error marks the doc 'error'.
- run_corpus nodes run once in a single worker over all docs (the timeout covers the call).
- On completion: coverage check, finalize the version (stats incl. metrics.doc_metrics totals),
  record run metrics. On cancel or crash: abandon building versions; cached outputs remain, so a
  rerun reuses finished docs.
- Only one active run per (dag, node): a new run whose nodes overlap an active run waits as
  'queued'; queued runs with an identical request merge into one.
- Workers run with obs.configure_environment(offline=True) unless `online` is set.

Workers write output objects themselves (ObjectStore writes are atomic and idempotent); the
parent is the only writer of the catalog and the run log. When every doc of a node is cached
or skipped and the result equals the latest version of that node (same fingerprint, snapshot,
input versions, checks fingerprint, scratch flag and doc outputs), that version is reused
instead of creating a new one; RunResult.versions then names the reused version.
"""

from __future__ import annotations

import argparse
import logging
import multiprocessing
import os
import subprocess
import sys
import time
import traceback
from collections import deque
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass, replace
from multiprocessing.connection import wait
from pathlib import Path
from typing import Any

import psutil

from ingest import gitinfo, obs
from ingest.config import Config, load_config
from ingest.contract import Doc, Issue
from ingest.engine import worker
from ingest.engine.planner import (
    NodePlan,
    PlanError,
    RunPlan,
    RunRequest,
    cache_lookup,
    head_tree,
    input_keyed,
    materialize_dag_files,
    plan_run,
    request_from_dict,
    request_to_dict,
    script_info,
)
from ingest.fingerprint import checks_fingerprint, dag_fingerprints, env_hash, sha256_file
from ingest.scripts import LoadedChecks, load_checks
from ingest.sources import snapshot_docs
from ingest.store.catalog import Catalog, RunStatus, VersionDocInput, VersionRow
from ingest.store.objects import ObjectStore
from ingest.validation import coverage, quality_checks, script_checks

_log = logging.getLogger("ingest.engine")

POLL_S = 0.5
"""How often the parent checks for cancellation and queue turns."""
STOP_WAIT_S = 5.0
EXECUTOR_OUT = "executor.out"


@dataclass(frozen=True)
class RunResult:
    run_id: int
    status: RunStatus
    versions: Mapping[str, int]
    """Node → version id produced (or reused, when unchanged)."""


def execute(
    catalog: Catalog,
    config: Config,
    plan: RunPlan,
    *,
    run_id: int | None = None,
    online: bool = False,
    log_level: str = "INFO",
    code_root: Path | None = None,
) -> RunResult:
    """Run in the current process (which manages the workers). Creates the run row unless
    `run_id` is given. Blocks until finished or cancelled.

    `code_root` is a materialized HEAD tree for committed runs (the one the plan was made
    from); without it a committed run materializes HEAD itself and fails if HEAD moved."""
    names = [n.node for n in plan.nodes]
    if run_id is None:
        run_id = catalog.create_run(
            plan.dag.name,
            {**request_to_dict(plan.request), "nodes": names},
            scratch=plan.scratch,
            commit=plan.commit,
            dirty=_version_dirty(plan),
        )
    catalog.add_event(run_id, "run_queued", {"executor_pid": os.getpid(), "nodes": names})
    runlog = obs.RunLog(config.run_dir(run_id), run_id=run_id, dag=plan.dag.name)
    try:
        return _Execution(catalog, config, plan, run_id, runlog, online, log_level, code_root).run()
    finally:
        runlog.close()


def run(config: Config, request: RunRequest, *, online: bool = False, log_level: str = "INFO") -> RunResult:
    """Plan and execute in the foreground (CLI)."""
    catalog = Catalog(config.catalog_path)
    try:
        with head_tree(config.repo_root) as head:
            plan = plan_run(catalog, config, request, code_root=head)
            root = head if plan.code == "committed" else None
            return execute(catalog, config, plan, online=online, log_level=log_level, code_root=root)
    finally:
        catalog.close()


def submit(config: Config, request: RunRequest, *, online: bool = False, log_level: str = "INFO") -> int:
    """Create a queued run and start a detached subprocess (`python -m ingest.engine.executor
    <run_id>`) that plans and executes it; returns the run id immediately (API).

    Plans once up front so a bad request raises PlanError here. Returns the id of an already
    queued run with an identical request instead of creating another."""
    catalog = Catalog(config.catalog_path)
    try:
        wanted = request_to_dict(request)
        for r in catalog.active_runs(request.dag):
            if r.status == "queued" and {k: v for k, v in r.request.items() if k != "nodes"} == wanted:
                return r.id
        with head_tree(config.repo_root) as head:
            plan = plan_run(catalog, config, request, code_root=head)
        run_id = catalog.create_run(
            plan.dag.name,
            {**wanted, "nodes": [n.node for n in plan.nodes]},
            scratch=plan.scratch,
            commit=plan.commit,
            dirty=_version_dirty(plan),
        )
        run_dir = config.run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        args = [sys.executable, "-m", "ingest.engine.executor", str(run_id), "--repo", str(config.repo_root)]
        args += ["--log-level", log_level] + (["--online"] if online else [])
        with open(run_dir / EXECUTOR_OUT, "ab") as out:
            proc = subprocess.Popen(
                args,
                cwd=config.repo_root,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env={**os.environ, "INGEST_DATA_DIR": str(config.data_dir)},
            )
        catalog.add_event(run_id, "run_submitted", {"executor_pid": proc.pid})
        return run_id
    finally:
        catalog.close()


def cancel(catalog: Catalog, run_id: int) -> None:
    """Request cancellation; the executor stops dispatching, kills workers and abandons
    building versions."""
    catalog.request_cancel(run_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ingest.engine.executor")
    parser.add_argument("run_id", type=int)
    parser.add_argument("--repo", type=Path, default=None)
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    obs.configure_logging(args.log_level)
    config = load_config(args.repo)
    catalog = Catalog(config.catalog_path)
    try:
        request = request_from_dict(catalog.get_run(args.run_id).request)
        try:
            with head_tree(config.repo_root) as head:
                plan = plan_run(catalog, config, request, code_root=head)
                root = head if plan.code == "committed" else None
                result = execute(
                    catalog, config, plan, run_id=args.run_id, online=args.online, log_level=args.log_level, code_root=root
                )
        except PlanError as e:
            catalog.set_run_status(args.run_id, "failed", str(e))
            catalog.add_event(args.run_id, "run_finished", {"status": "failed", "error": str(e)})
            return 1
        return 0 if result.status == "succeeded" else 1
    finally:
        catalog.close()


def _version_dirty(plan: RunPlan) -> bool:
    """Whether the executed code differs from the recorded commit."""
    return plan.code == "working" and plan.dirty


class _Cancelled(Exception):
    pass


class _RunError(Exception):
    pass


@dataclass(frozen=True)
class _Out:
    status: str
    sha: str | None
    kind: str | None


def _pid_alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


class _Execution:
    def __init__(
        self,
        catalog: Catalog,
        config: Config,
        plan: RunPlan,
        run_id: int,
        runlog: obs.RunLog,
        online: bool,
        log_level: str,
        code_root: Path | None,
    ) -> None:
        self.catalog = catalog
        self.config = config
        self.plan = plan
        self.run_id = run_id
        self.runlog = runlog
        self.online = online
        self.log_level = log_level
        self.code_root = code_root
        self.dag = plan.dag
        self.objects = ObjectStore(config.objects_dir)
        self.mp = multiprocessing.get_context("spawn")
        self.versions: dict[str, int] = {}
        self.outputs: dict[str, dict[str, _Out]] = {}
        self.failed: dict[str, str] = {}
        """Node → why it failed or was not run."""
        self._last_poll = 0.0

    # Events and cancellation

    def emit(
        self,
        event: str,
        *,
        node: str | None = None,
        version_id: int | None = None,
        doc_id: str | None = None,
        level: str = "INFO",
        message: str = "",
        store: bool = True,
        **fields: Any,
    ) -> None:
        self.runlog.event(event, level=level, message=message, node=node, version_id=version_id, doc_id=doc_id, **fields)
        if store:
            payload = {k: v for k, v in (("node", node), ("version_id", version_id), ("doc_id", doc_id)) if v is not None}
            self.catalog.add_event(self.run_id, event, {**payload, **fields})
        if message and doc_id is None:
            _log.log(obs.LEVELS[level], message)

    def poll_cancel(self, *, now: bool = False) -> None:
        t = time.monotonic()
        if not now and t - self._last_poll < POLL_S:
            return
        self._last_poll = t
        if self.catalog.get_run(self.run_id).cancel_requested:
            raise _Cancelled

    # Run

    def run(self) -> RunResult:
        status: RunStatus = "succeeded"
        error: str | None = None
        try:
            self._wait_turn()
            with ExitStack() as stack:
                root = self._resolve_code(stack)
                self._check_plan(root)
                self.catalog.set_run_status(self.run_id, "running")
                self.emit(
                    "run_started",
                    message=f"run {self.run_id} started: {self.dag.name} ({', '.join(n.node for n in self.plan.nodes)})",
                    executor_pid=os.getpid(),
                    code=self.plan.code,
                    commit=self.plan.commit,
                    scratch=self.plan.scratch,
                    scratch_reasons=list(self.plan.scratch_reasons),
                    snapshot_id=self.plan.snapshot_id,
                    docs=len(self.plan.doc_ids),
                )
                docs = self._docs()
                for node_plan in self.plan.nodes:
                    _NodeRun(self, node_plan, root, docs).run()
            if self.failed:
                status = "failed"
                error = "; ".join(f"{n}: {why}" for n, why in self.failed.items())
        except _Cancelled:
            status = "cancelled"
        except KeyboardInterrupt:
            self._finish("cancelled", "interrupted")
            raise
        except Exception as e:
            status, error = "failed", f"{type(e).__name__}: {e}"
            self.runlog.event("engine_error", level="ERROR", message=error, traceback=traceback.format_exc())
        self._finish(status, error)
        return RunResult(self.run_id, status, dict(self.versions))

    def _finish(self, status: RunStatus, error: str | None) -> None:
        for v in self.catalog.run_versions(self.run_id):
            if v.status == "building":
                self.catalog.abandon_version(v.id)
        self.catalog.set_run_status(self.run_id, status, error)
        self.emit(
            "run_finished",
            level="INFO" if status == "succeeded" else "WARNING",
            message=f"run {self.run_id} {status}" + (f": {error}" if error else ""),
            status=status,
            error=error,
            versions=dict(self.versions),
        )

    def _wait_turn(self) -> None:
        """Wait while an overlapping run is running, or queued ahead of this one."""
        mine = {n.node for n in self.plan.nodes}
        pids: dict[int, int | None] = {}
        waiting = False
        while True:
            self.poll_cancel(now=True)
            blockers = []
            for r in self.catalog.active_runs(self.dag.name):
                if r.id == self.run_id or not (r.status == "running" or r.id < self.run_id):
                    continue
                nodes = r.request.get("nodes")
                if nodes is not None and mine.isdisjoint(nodes):
                    continue
                if r.id not in pids:
                    found = [e.payload["executor_pid"] for e in self.catalog.events(r.id) if "executor_pid" in e.payload]
                    pids[r.id] = found[-1] if found else None
                pid = pids[r.id]
                if pid is not None and not _pid_alive(pid):
                    self._reap(r.id)
                    continue
                blockers.append(r.id)
            if not blockers:
                return
            if not waiting:
                waiting = True
                self.emit("run_waiting", message=f"run {self.run_id} waits for run(s) {blockers}", blocking=blockers)
            time.sleep(POLL_S)

    def _reap(self, run_id: int) -> None:
        """Mark a run whose executor process is gone as failed."""
        for v in self.catalog.run_versions(run_id):
            if v.status == "building":
                self.catalog.abandon_version(v.id)
        self.catalog.set_run_status(run_id, "failed", "executor process exited")

    def _resolve_code(self, stack: ExitStack) -> Path:
        if self.plan.code == "working":
            return self.config.repo_root
        if self.code_root is not None:
            return self.code_root
        repo = self.config.repo_root
        head = stack.enter_context(head_tree(repo))
        if head is None or gitinfo.head_commit(repo) != self.plan.commit:
            raise _RunError("HEAD moved since the run was planned; plan again")
        materialize_dag_files(repo, head, self.dag)
        return head

    def _check_plan(self, root: Path) -> None:
        overrides = {
            ref.node: ref.fingerprint
            for n in self.plan.nodes
            for ref in n.inputs.values()
            if ref.version_id is not None
        }
        env = env_hash(self.config.repo_root)
        fps = dag_fingerprints(self.dag, root, env, overrides)
        changed = [n.node for n in self.plan.nodes if fps[n.node] != n.fingerprint]
        if env != self.plan.env or changed:
            raise _RunError(f"code changed since the run was planned ({', '.join(changed) or 'environment'}); plan again")

    def _docs(self) -> dict[str, Doc]:
        wanted = set(self.plan.doc_ids)
        source = self.config.sources[self.dag.source]
        by_id = {d.id: d for d in snapshot_docs(self.catalog, self.plan.snapshot_id, source) if d.id in wanted}
        return {d: by_id[d] for d in self.plan.doc_ids}


class _Worker:
    """Parent-side handle of one worker process."""

    def __init__(self, mp: Any, init: worker.WorkerInit) -> None:
        self.conn, child = mp.Pipe(duplex=True)
        self.proc = mp.Process(target=worker.main, args=(child, init), name=f"ingest-{init.node}")
        self.proc.start()
        child.close()
        self.loaded = False
        self.ready = False
        self.task: worker.DocTask | worker.CorpusTask | None = None
        self.started = 0.0
        self.deadline: float | None = None
        self.eof = False

    @property
    def pid(self) -> int | None:
        return self.proc.pid

    def doc_ids(self) -> list[str]:
        if isinstance(self.task, worker.DocTask):
            return [self.task.doc.id]
        return [d.id for d in self.task.docs] if self.task is not None else []

    def assign(self, task: worker.DocTask | worker.CorpusTask, timeout: float) -> None:
        self.conn.send(task)
        self.task = task
        self.started = time.monotonic()
        self.deadline = self.started + timeout

    def messages(self) -> list[tuple[str, Any]]:
        out = []
        while not self.eof:
            try:
                if not self.conn.poll():
                    break
                out.append(self.conn.recv())
            except (EOFError, OSError):
                self.eof = True
        return out

    def exited(self) -> bool:
        if self.eof:
            self.proc.join(timeout=STOP_WAIT_S)
        return not self.proc.is_alive()

    def kill(self) -> None:
        if self.proc.is_alive():
            self.proc.kill()
        self.proc.join(timeout=STOP_WAIT_S)
        self.conn.close()

    def stop(self) -> None:
        try:
            self.conn.send(None)
        except OSError:
            pass
        self.proc.join(timeout=STOP_WAIT_S)
        self.kill()


class _NodeFailed(Exception):
    def __init__(self, error_type: str, message: str, tb: str | None) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.message = message
        self.traceback = tb


class _NodeRun:
    def __init__(self, ex: _Execution, plan: NodePlan, root: Path, docs: Mapping[str, Doc]) -> None:
        self.ex = ex
        self.plan = plan
        self.name = plan.node
        self.spec = ex.dag.nodes[plan.node]
        self.root = root
        self.docs = docs
        self.catalog = ex.catalog
        self.kind = script_info(root / self.spec.script)[0]
        self.keyed = self.name in input_keyed(ex.dag, {n: script_info(root / s.script)[0] for n, s in ex.dag.nodes.items()})
        self.force = ex.plan.request.recompute == "force"
        self.checks_fp = (
            checks_fingerprint(sha256_file(root / self.spec.checks), self.spec.params) if self.spec.checks else None
        )
        self.version: VersionRow | None = None
        self.keys: dict[str, str] = {}
        self.out: dict[str, _Out] = {}
        self.vdocs: dict[str, VersionDocInput] = {}
        self.issues: dict[str, list[tuple[str | None, Issue]]] = {"hard": [], "quality": [], "script": []}
        self.metric_rows: list[tuple[str | None, str | None, str, float]] = []
        self.model_load_s: list[float] = []
        self.processed: set[str] = set()
        self.pending_lines: list[dict[str, Any]] = []
        self.input_versions: Mapping[str, int] = {}
        self.start = time.perf_counter()

    def run(self) -> None:
        ex = self.ex
        upstream_failed = [i for i, ref in self.plan.inputs.items() if ref.version_id is None and i in ex.failed]
        if upstream_failed:
            ex.failed[self.name] = f"not run: input {', '.join(upstream_failed)} failed"
            ex.emit("node_skipped", node=self.name, level="WARNING", message=f"{self.name}: {ex.failed[self.name]}")
            return
        ex.poll_cancel(now=True)

        inputs, input_versions = self._inputs()
        blocked: dict[str, str] = {}
        input_shas: dict[str, dict[str, str]] = {}
        for doc_id in self.docs:
            reason = None
            for name, outs in inputs.items():
                o = outs.get(doc_id)
                if o is None or o.status != "ok" or o.sha is None:
                    reason = f"input {name} is {o.status if o else 'missing'}"
                    break
            if reason:
                blocked[doc_id] = reason
            else:
                input_shas[doc_id] = {name: outs[doc_id].sha for name, outs in inputs.items()}  # type: ignore[misc]
        unblocked = [d for d in self.docs if d not in blocked]
        self.keys, entries = cache_lookup(self.catalog, self.plan.fingerprint, self.kind, self.keyed, unblocked, input_shas)
        if self.force:
            entries = {}
        todo = [d for d in unblocked if d not in entries]
        ex.emit(
            "node_planned",
            node=self.name,
            store=False,
            docs=len(self.docs),
            cached=len(entries),
            to_process=len(todo),
            skipped=len(blocked),
        )

        if not todo and self._reuse(entries, blocked, input_versions):
            return

        self.input_versions = input_versions
        workers: list[_Worker] = []
        completed = False
        try:
            init = self._init()
            describe = None
            if todo:
                n = 1 if self.kind == "corpus" else max(1, min(self.spec.workers, len(todo)))
                workers = [_Worker(ex.mp, init) for _ in range(n)]
            else:
                describe = self._known_describe()
                if describe is None:
                    workers = [_Worker(ex.mp, replace(init, describe_only=True))]
            try:
                if describe is not None:
                    self._create_version(describe)
                else:
                    self._drive(workers, deque(), until_version=True)
                self._record_cached(entries)
                self._record_blocked(blocked)
                if todo:
                    self._drive(workers, deque(self._tasks(todo, inputs)), until_version=False)
            except _NodeFailed as e:
                self._fail(e, todo)
                return
            completed = True
        finally:
            for w in workers:
                w.stop() if completed and w.ready else w.kill()
        self._finalize()

    # Inputs and caching

    def _inputs(self) -> tuple[dict[str, dict[str, _Out]], dict[str, int]]:
        outs: dict[str, dict[str, _Out]] = {}
        versions: dict[str, int] = {}
        for name, ref in self.plan.inputs.items():
            if ref.version_id is None:
                outs[name] = self.ex.outputs[name]
                versions[name] = self.ex.versions[name]
            else:
                outs[name] = {
                    d.doc_id: _Out(d.status, d.object_sha, d.kind) for d in self.catalog.version_docs(ref.version_id)
                }
                versions[name] = ref.version_id
        return outs, versions

    def _tasks(self, todo: list[str], inputs: Mapping[str, Mapping[str, _Out]]) -> list[worker.DocTask | worker.CorpusTask]:
        assert self.version is not None

        def objs(doc_id: str) -> dict[str, worker.InputObject]:
            return {n: worker.InputObject(o[doc_id].sha, o[doc_id].kind) for n, o in inputs.items()}  # type: ignore[arg-type]

        if self.kind == "corpus":
            corpus_inputs = {n: {d: worker.InputObject(o[d].sha, o[d].kind) for d in todo} for n, o in inputs.items()}  # type: ignore[arg-type]
            return [worker.CorpusTask([self.docs[d] for d in todo], corpus_inputs, self.version.id)]
        return [worker.DocTask(self.docs[d], objs(d), self.version.id) for d in todo]

    def _reuse(self, entries: Mapping[str, Any], blocked: Mapping[str, str], input_versions: Mapping[str, int]) -> bool:
        """Reuse the latest version when this run would rebuild it unchanged."""
        ex = self.ex
        latest = next(
            (v for v in self.catalog.list_versions(ex.dag.name, self.name) if v.scratch == ex.plan.scratch), None
        )
        if latest is None or (latest.node_fp, latest.snapshot_id) != (self.plan.fingerprint, ex.plan.snapshot_id):
            return False
        if dict(latest.input_versions) != dict(input_versions) or latest.meta.get("checks_fp") != self.checks_fp:
            return False
        expected = {d: e.object_sha for d, e in entries.items()} | dict.fromkeys(blocked)
        docs = self.catalog.version_docs(latest.id)
        if {d.doc_id: (d.object_sha if d.status != "skipped" else None) for d in docs} != expected:
            return False
        ex.versions[self.name] = latest.id
        ex.outputs[self.name] = {d.doc_id: _Out(d.status, d.object_sha, d.kind) for d in docs}
        wall = time.perf_counter() - self.start
        counts = {s: sum(1 for d in docs if d.status == s) for s in ("ok", "error", "skipped")}
        self._node_metrics(wall, processed=0, cached=len(entries), failed=counts["error"], skipped=counts["skipped"])
        self.catalog.add_metrics(ex.run_id, self.metric_rows)
        ex.emit(
            "node_finished",
            node=self.name,
            version_id=latest.id,
            message=f"{self.name}: unchanged, reusing {latest.label}",
            reused=True,
            version=latest.label,
            wall_s=wall,
            processed=0,
            cached=len(entries),
            **counts,
        )
        return True

    def _known_describe(self) -> dict[str, Any] | None:
        """describe() output of an earlier version with this fingerprint."""
        for v in self.catalog.list_versions(self.ex.dag.name, self.name):
            if v.node_fp == self.plan.fingerprint and "describe" in v.meta:
                return dict(v.meta["describe"])
        return None

    # Versions

    def _init(self) -> worker.WorkerInit:
        ex = self.ex
        return worker.WorkerInit(
            run_id=ex.run_id,
            dag=ex.dag.name,
            node=self.name,
            run_dir=ex.config.run_dir(ex.run_id),
            objects_dir=ex.config.objects_dir,
            script=self.root / self.spec.script,
            checks=self.root / self.spec.checks if self.spec.checks else None,
            params=dict(self.spec.params),
            scratch=ex.plan.scratch,
            log_level=ex.log_level,
            offline=not ex.online,
        )

    def _create_version(self, describe: Mapping[str, Any]) -> None:
        ex = self.ex
        input_versions = self.input_versions
        meta = {
            "script": self.spec.script,
            "script_hash": sha256_file(self.root / self.spec.script),
            "params": dict(self.spec.params),
            "env": ex.plan.env,
            "describe": dict(describe),
            "checks": self.spec.checks,
            "checks_fp": self.checks_fp,
            "timeout": self.spec.timeout,
            "workers": self.spec.workers,
            "code": ex.plan.code,
        }
        self.version = self.catalog.create_version(
            dag=ex.dag.name,
            node=self.name,
            node_fp=self.plan.fingerprint,
            snapshot_id=ex.plan.snapshot_id,
            input_versions=input_versions,
            run_id=ex.run_id,
            commit=ex.plan.commit,
            dirty=_version_dirty(ex.plan),
            scratch=ex.plan.scratch,
            artifact_type=self.plan.artifact_type,
            meta=meta,
        )
        ex.emit(
            "node_started",
            node=self.name,
            version_id=self.version.id,
            message=f"{self.name}: started",
            fingerprint=self.plan.fingerprint,
            input_versions=dict(input_versions),
        )
        self._write_lines(self.pending_lines)
        self.pending_lines = []

    def _write_lines(self, lines: list[dict[str, Any]]) -> None:
        if self.version is None:
            self.pending_lines.extend(lines)
            return
        for line in lines:
            if line.get("version_id") is None:
                line["version_id"] = self.version.id
        self.ex.runlog.write_lines(lines)

    def _add_doc(self, doc: VersionDocInput, event: str, **fields: Any) -> None:
        assert self.version is not None
        self.vdocs[doc.doc_id] = doc
        self.out[doc.doc_id] = _Out(doc.status, doc.object_sha, doc.kind)
        level = "ERROR" if event == "doc_failed" else "INFO"
        with self.catalog.transaction():
            self.catalog.add_version_docs(self.version.id, [doc])
            self.ex.emit(event, node=self.name, version_id=self.version.id, doc_id=doc.doc_id, level=level, **fields)

    def _record_cached(self, entries: Mapping[str, Any]) -> None:
        checks = load_checks(self.root / self.spec.checks) if self.spec.checks else None
        atype = self.plan.artifact_type
        for doc_id, entry in entries.items():
            self.ex.poll_cancel()
            quality, script = self._check_cached(atype, checks, entry.object_sha, entry.kind)
            errors = [i for i in script if i.level == "error"]
            self.issues["quality"] += [(doc_id, i) for i in quality]
            self.issues["script"] += [(doc_id, i) for i in script]
            doc = VersionDocInput(
                doc_id=doc_id,
                status="error" if errors else "ok",
                object_sha=entry.object_sha,
                kind=entry.kind,
                cached=True,
                error_type="CheckFailed" if errors else None,
                error_message="; ".join(f"{i.code}: {i.message}" for i in errors) or None,
                duration_s=entry.duration_s,
                metrics=entry.metrics,
            )
            self._add_doc(doc, "doc_cached", status=doc.status)

    def _check_cached(
        self, atype: Any, checks: LoadedChecks | None, sha: str, kind: str
    ) -> tuple[list[Issue], list[Issue]]:
        """Quality and script checks of a cached record (hard checks passed when it was cached)."""
        if atype not in ("parse", "chunk", "embed") and checks is None:
            return [], []
        record = self.ex.objects.get(sha, kind)  # type: ignore[arg-type]
        params = dict(self.spec.params)
        return quality_checks(atype, record, params), script_checks(checks, record, params) if checks else []

    def _record_blocked(self, blocked: Mapping[str, str]) -> None:
        for doc_id, reason in blocked.items():
            self._add_doc(
                VersionDocInput(doc_id=doc_id, status="skipped", error_message=reason), "doc_skipped", reason=reason
            )

    # Workers

    def _drive(self, workers: list[_Worker], pending: deque[Any], *, until_version: bool) -> None:
        """Feed tasks to workers until all are done (or, with `until_version`, until the first
        worker has reported describe() and the version exists). Raises _NodeFailed when a
        worker fails or dies before setup() completes."""
        ex = self.ex
        init = self._init()
        while True:
            if until_version and self.version is not None:
                return
            if not until_version and not pending and all(w.task is None for w in workers):
                return
            ex.poll_cancel()
            if self.version is not None:
                for w in workers:
                    if w.ready and w.task is None and pending:
                        task = pending.popleft()
                        w.assign(task, self.spec.timeout)
                        for doc_id in w.doc_ids():
                            ex.emit("doc_started", node=self.name, version_id=self.version.id, doc_id=doc_id, store=False, pid=w.pid)
            now = time.monotonic()
            timeout = min([POLL_S] + [max(0.0, w.deadline - now) for w in workers if w.deadline is not None])
            wait([w.conn for w in workers if not w.eof] + [w.proc.sentinel for w in workers], timeout)
            for w in list(workers):
                for kind, msg in w.messages():
                    self._handle(w, kind, msg)
                if w.exited():
                    lost = w.task is not None
                    if self.version is None or (not w.ready and not until_version):
                        raise _NodeFailed("WorkerCrashed", f"worker {w.pid} exited with code {w.proc.exitcode} during setup", None)
                    if lost:
                        self._lost(w, "WorkerCrashed", f"worker {w.pid} exited with code {w.proc.exitcode}")
                    w.kill()
                    workers.remove(w)
                    if pending:
                        workers.append(_Worker(ex.mp, init))
                elif w.deadline is not None and time.monotonic() > w.deadline:
                    w.kill()
                    self._lost(w, "Timeout", f"no result within the {self.spec.timeout:g} s timeout; worker {w.pid} killed")
                    workers.remove(w)
                    if pending:
                        workers.append(_Worker(ex.mp, init))

    def _handle(self, w: _Worker, kind: str, msg: Any) -> None:
        if kind == "loaded":
            w.loaded = True
            if self.version is None:
                self._create_version(msg.describe)
            self._write_lines(msg.lines)
        elif kind == "ready":
            w.ready = True
            self.model_load_s.append(msg.model_load_s)
            self._write_lines(msg.lines)
        elif kind == "failed":
            self._write_lines(msg.lines)
            if self.version is None:
                self._create_version({})
            raise _NodeFailed(msg.error_type, f"{msg.stage} failed: {msg.error_type}: {msg.error_message}", msg.traceback)
        elif kind == "batch":
            self._write_lines(msg.lines)
            for result in msg.results:
                self._result(result)
            w.task = None
            w.deadline = None

    def _result(self, r: worker.DocResult) -> None:
        assert self.version is not None
        self.processed.add(r.doc_id)
        self.issues["hard"] += [(r.doc_id, i) for i in r.hard]
        self.issues["quality"] += [(r.doc_id, i) for i in r.quality]
        self.issues["script"] += [(r.doc_id, i) for i in r.script]
        doc = VersionDocInput(
            doc_id=r.doc_id,
            status=r.status,
            object_sha=r.object_sha,
            kind=r.kind,
            error_type=r.error_type,
            error_message=r.error_message,
            traceback=r.traceback,
            duration_s=r.duration_s,
            peak_rss_mb=r.peak_rss_mb,
            worker_pid=r.pid,
            metrics=r.metrics,
        )
        self._doc_metrics(doc)
        with self.catalog.transaction():
            if r.object_sha is not None and r.kind is not None:
                self.catalog.cache_put(self.keys[r.doc_id], r.doc_id, r.object_sha, r.kind, r.duration_s, r.metrics)
            fields: dict[str, Any] = {"status": r.status, "duration_s": r.duration_s, "pid": r.pid}
            if r.status == "error":
                fields |= {"error_type": r.error_type, "error": r.error_message}
            self._add_doc(doc, "doc_finished" if r.status == "ok" else "doc_failed", **fields)

    def _lost(self, w: _Worker, error_type: str, message: str) -> None:
        """Fail the docs of a worker's task after a timeout or crash."""
        duration = time.monotonic() - w.started
        for doc_id in w.doc_ids():
            self.processed.add(doc_id)
            doc = VersionDocInput(
                doc_id=doc_id,
                status="error",
                error_type=error_type,
                error_message=message,
                duration_s=duration,
                worker_pid=w.pid,
            )
            self._doc_metrics(doc)
            self._add_doc(doc, "doc_failed", status="error", error_type=error_type, error=message, pid=w.pid)
        w.task = None
        w.deadline = None

    def _fail(self, e: _NodeFailed, todo: list[str]) -> None:
        """A setup failure: unfinished docs get its error, the version is abandoned."""
        ex = self.ex
        if self.version is None:
            self._create_version({})
        assert self.version is not None
        for doc_id in todo:
            if doc_id not in self.vdocs:
                self._add_doc(
                    VersionDocInput(doc_id=doc_id, status="error", error_type=e.error_type, error_message=e.message, traceback=e.traceback),
                    "doc_failed",
                    status="error",
                    error_type=e.error_type,
                    error=e.message,
                )
        self.catalog.abandon_version(self.version.id)
        self.catalog.add_metrics(ex.run_id, self.metric_rows)
        ex.failed[self.name] = e.message
        ex.emit(
            "node_failed",
            node=self.name,
            version_id=self.version.id,
            level="ERROR",
            message=f"{self.name}: {e.message}",
            error_type=e.error_type,
            error=e.message,
            traceback=e.traceback,
        )

    # Finalize

    def _doc_metrics(self, doc: VersionDocInput) -> None:
        if doc.duration_s is not None:
            self.metric_rows.append((self.name, doc.doc_id, "duration_s", doc.duration_s))
        if doc.peak_rss_mb is not None:
            self.metric_rows.append((self.name, doc.doc_id, "peak_rss_mb", doc.peak_rss_mb))

    def _node_metrics(self, wall: float, *, processed: int, cached: int, failed: int, skipped: int) -> None:
        considered = processed + cached
        rows = {
            "wall_s": wall,
            "docs_processed": processed,
            "docs_cached": cached,
            "docs_failed": failed,
            "docs_skipped": skipped,
            "cache_hit_rate": cached / considered if considered else 0.0,
            "failure_rate": failed / considered if considered else 0.0,
        }
        if self.model_load_s:
            rows["model_load_s"] = max(self.model_load_s)
        if self.plan.artifact_type == "parse":
            done = [self.vdocs[d] for d in self.processed if self.vdocs[d].status == "ok"]
            pages = sum(d.metrics.get("pages", 0.0) for d in done)
            rows["pages"] = pages
            rows["ocr_pages"] = sum(d.metrics.get("ocr_pages", 0.0) for d in done)
            rows["pages_per_s"] = pages / wall if wall > 0 else 0.0
        self.metric_rows += [(self.name, None, k, float(v)) for k, v in rows.items()]

    def _finalize(self) -> None:
        ex = self.ex
        assert self.version is not None
        vid = self.version.id
        docs = self.catalog.version_docs(vid)
        self.issues["hard"] += coverage(ex.plan.doc_ids, docs)
        ok = [d for d in docs if d.status == "ok"]
        totals: dict[str, float] = {}
        for d in ok:
            for k, v in d.metrics.items():
                totals[k] = totals.get(k, 0.0) + v
        wall = time.perf_counter() - self.start
        cached = sum(1 for d in docs if d.cached)
        counts = {s: sum(1 for d in docs if d.status == s) for s in ("ok", "error", "skipped")}
        stats = {
            "docs": len(docs),
            **counts,
            "cached": cached,
            "processed": len(self.processed),
            "size_bytes": sum(ex.objects.size(d.object_sha, d.kind) for d in ok if d.object_sha and d.kind),  # type: ignore[arg-type]
            "wall_s": wall,
            "totals": totals,
        }
        self._node_metrics(
            wall, processed=len(self.processed), cached=cached, failed=counts["error"], skipped=counts["skipped"]
        )
        with self.catalog.transaction():
            self.catalog.add_issues(vid, "hard", self.issues["hard"])
            self.catalog.add_issues(vid, "quality", self.issues["quality"])
            self.catalog.add_issues(vid, "script", self.issues["script"], checks_fp=self.checks_fp)
            version = self.catalog.finalize_version(vid, stats)
            self.catalog.add_metrics(ex.run_id, self.metric_rows)
        ex.versions[self.name] = vid
        ex.outputs[self.name] = self.out
        ex.emit(
            "node_finished",
            node=self.name,
            version_id=vid,
            message=f"{self.name}: {version.label} ok {counts['ok']} · error {counts['error']} · skipped {counts['skipped']} · cached {cached} ({wall:.1f}s)",
            reused=False,
            version=version.label,
            wall_s=wall,
            processed=len(self.processed),
            cached=cached,
            **counts,
        )


if __name__ == "__main__":
    raise SystemExit(main())
