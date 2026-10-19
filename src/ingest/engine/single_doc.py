"""Single-doc runs (Ingestion-Design §8): process one document for the query app's uploads
through the node chain recorded in the current embed version's lineage — its scripts at the
lineage's git commit, params and model revisions — so outputs land in the per-doc cache under the
same fingerprints. No version is created and no per-node lock is taken. One warm child process
keeps the chain's models loaded and processes queued docs one at a time, first in first out.

Each submitted doc gets a run (catalog kind "single_doc") whose catalog events are:
    queued, doc_started, node_finished {node, metrics (e.g. pages, chunks, vectors)},
    doc_finished {doc_id}, doc_failed {node, error_type, message}
and the run ends succeeded or failed. On success the doc is recorded with
Catalog.add_pending_doc against the embed version, so publish.library includes it.

Per doc and lineage node: the cache entry for (node fingerprint, doc) is reused when present
(node_finished has cached=True); otherwise the child runs the node with the worker's capture and
checks, the parent writes the cache entry. An exception, hard- or script-check error, timeout
(Timeout) or child crash (WorkerCrashed) fails the doc; the child is restarted and the next doc
is served. Submitting a doc takes it out of removed_docs (it was uploaded again). close() cancels
docs still queued or in flight (doc_failed with error_type Cancelled, run status cancelled).
"""

from __future__ import annotations

import copy
import json
import multiprocessing
import os
import tempfile
import threading
import time
import traceback
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from multiprocessing.connection import Connection, wait
from pathlib import Path
from typing import Any

from ingest import gitinfo, obs
from ingest.config import Config
from ingest.contract import Doc
from ingest.engine import worker
from ingest.engine.planner import script_info
from ingest.fingerprint import checks_fingerprint, sha256_file
from ingest.scripts import load_checks, load_script
from ingest.sources import check_pdf, meta_for
from ingest.store.catalog import Catalog, PendingDocInput, RunStatus, VersionRow
from ingest.store.objects import ObjectStore
from ingest.validation import script_checks

DEFAULT_TIMEOUT_S = 300.0
POLL_S = 0.5
"""How often waits check for close()."""
STOP_WAIT_S = 5.0


@dataclass(frozen=True)
class LineageNode:
    node: str
    version_id: int
    node_fp: str
    script: str
    script_hash: str
    params: Mapping[str, Any]
    commit: str | None
    checks: str | None
    timeout: float
    inputs: tuple[str, ...] = ()
    """Input node names (sorted), each earlier in the lineage."""


def lineage(catalog: Catalog, dag: str = "default", node: str = "embed") -> list[LineageNode]:
    """The node chain (in topo order, ending with `node`) recorded in the current version of
    dag/node via input_versions. Raises LookupError when there is no current version."""
    top = catalog.current_version(dag, node)
    if top is None:
        raise LookupError(f"{dag}/{node} has no current version")
    out: dict[str, LineageNode] = {}

    def visit(v: VersionRow) -> None:
        if v.node in out:
            if out[v.node].version_id != v.id:
                raise LookupError(f"{dag}/{node}: lineage reads two versions of {v.node}")
            return
        for name in sorted(v.input_versions):
            visit(catalog.get_version(v.input_versions[name]))
        meta = v.meta
        out[v.node] = LineageNode(
            node=v.node,
            version_id=v.id,
            node_fp=v.node_fp,
            script=meta["script"],
            script_hash=meta["script_hash"],
            params=dict(meta.get("params") or {}),
            commit=v.commit,
            checks=meta.get("checks"),
            timeout=float(meta.get("timeout") or DEFAULT_TIMEOUT_S),
            inputs=tuple(sorted(v.input_versions)),
        )

    visit(top)
    return list(out.values())


def docs_to_reprocess(catalog: Catalog, config: Config, dag: str = "default", node: str = "embed") -> list[str]:
    """Upload docs that aren't in the current version and aren't pending against it (e.g. after a
    promotion), excluding removed docs. The query app resubmits these.

    Uploads are docs with a path in the uploads folder, from the latest snapshot of the version's
    source and from pending docs of any dag/node version; order: snapshot order, then oldest
    pending first. Raises LookupError when there is no current version."""
    current = catalog.current_version(dag, node)
    if current is None:
        raise LookupError(f"{dag}/{node} has no current version")
    prefix = f"{config.uploads_dir.name}/"
    source = catalog.get_snapshot(current.snapshot_id).source
    candidates: list[str] = []
    latest = catalog.latest_snapshot(source)
    if latest is not None:
        candidates += [d.doc_id for d in catalog.snapshot_docs(latest.id) if any(p.startswith(prefix) for p in d.paths)]
    versions: dict[int, VersionRow] = {}
    for p in catalog.pending_docs():
        if p.version_id not in versions:
            versions[p.version_id] = catalog.get_version(p.version_id)
        v = versions[p.version_id]
        if (v.dag, v.node) == (dag, node) and any(path.startswith(prefix) for path in p.paths):
            candidates.append(p.doc_id)
    done = {d.doc_id for d in catalog.version_docs(current.id) if d.status == "ok"}
    done |= {p.doc_id for p in catalog.pending_docs(current.id)}
    done |= catalog.removed_docs()
    return [d for d in dict.fromkeys(candidates) if d not in done]


# The resolved chain


@dataclass
class _Chain:
    """A lineage with its scripts materialized at the lineage commits."""

    nodes: list[LineageNode]
    root: tempfile.TemporaryDirectory[str]
    scripts: dict[str, Path]
    checks: dict[str, Path | None]
    artifact_types: dict[str, str | None]

    @property
    def top(self) -> LineageNode:
        return self.nodes[-1]

    def cleanup(self) -> None:
        self.root.cleanup()


def _resolve(config: Config, catalog: Catalog, dag: str, node: str) -> _Chain:
    """Lineage → scripts and checks files at each node's commit, hashes verified. Raises
    LookupError (no current version) or RuntimeError (unusable lineage)."""
    nodes = lineage(catalog, dag, node)
    root = tempfile.TemporaryDirectory(prefix="ingest-single-doc-")
    try:
        scripts: dict[str, Path] = {}
        checks: dict[str, Path | None] = {}
        types: dict[str, str | None] = {}
        for n in nodes:
            if n.commit is None:
                raise RuntimeError(f"{dag}/{n.node} v{n.version_id} has no git commit")
            dest = Path(root.name) / n.commit
            files = [n.script] + ([n.checks] if n.checks else [])
            try:
                missing = gitinfo.materialize_commit(config.repo_root, n.commit, files, dest)
            except ValueError as e:
                raise RuntimeError(f"{dag}/{n.node}: {e}") from None
            if missing:
                raise RuntimeError(f"{dag}/{n.node}: {', '.join(missing)} not found at commit {n.commit[:12]}")
            script = dest / n.script
            actual = sha256_file(script)
            if actual != n.script_hash:
                raise RuntimeError(
                    f"{dag}/{n.node}: {n.script} at commit {n.commit[:12]} has hash {actual[:12]}, "
                    f"but the lineage version recorded {n.script_hash[:12]}"
                )
            check = dest / n.checks if n.checks else None
            v = catalog.get_version(n.version_id)
            if check is not None and v.meta.get("checks_fp") not in (None, checks_fingerprint(sha256_file(check), n.params)):
                raise RuntimeError(f"{dag}/{n.node}: {n.checks} at commit {n.commit[:12]} differs from the lineage version's")
            kind, atype = script_info(script)
            if kind == "corpus":
                raise RuntimeError(f"{dag}/{n.node}: run_corpus nodes can't process a single doc")
            scripts[n.node], checks[n.node], types[n.node] = script, check, atype
        return _Chain(nodes, root, scripts, checks, types)
    except BaseException:
        root.cleanup()
        raise


# Child process


@dataclass(frozen=True)
class _ChildNode:
    node: str
    version_id: int
    script: Path
    checks: Path | None
    params: Mapping[str, Any]


@dataclass(frozen=True)
class _ChildInit:
    dag: str
    objects_dir: Path
    nodes: list[_ChildNode]
    offline: bool
    log_level: str


@dataclass(frozen=True)
class _NodeTask:
    node: str
    run_id: int
    run_dir: Path
    task: worker.DocTask


def _child_main(conn: Connection, init: _ChildInit) -> None:
    """Load every node and call setup() once, send ("ready", lines) or ("failed", node,
    worker.Failed), then answer each _NodeTask with ("batch", worker.Batch) until None."""
    obs.configure_environment(offline=init.offline)
    pid = os.getpid()
    store = ObjectStore(init.objects_dir)
    runners: dict[str, worker._Runner] = {}
    lines: list[dict[str, Any]] = []
    for n in init.nodes:
        winit = worker.WorkerInit(
            run_id=0,
            dag=init.dag,
            node=n.node,
            run_dir=Path(),
            objects_dir=init.objects_dir,
            script=n.script,
            checks=n.checks,
            params=dict(n.params),
            scratch=False,
            log_level=init.log_level,
            offline=init.offline,
        )
        stage: Any = "load"
        cap = obs.Captured()
        try:
            with worker._capture(winit, n.version_id, None) as cap:
                script = load_script(n.script)
                checks = load_checks(n.checks) if n.checks is not None else None
                stage = "describe"
                describe = dict(script.describe(copy.deepcopy(dict(n.params)))) if script.describe else {}
                describe = json.loads(json.dumps(describe, default=str))
                stage = "setup"
                state = script.setup(copy.deepcopy(dict(n.params))) if script.setup else None
        except Exception as e:
            conn.send(("failed", n.node, worker._failed(pid, stage, e, lines + cap.lines)))
            return
        lines += cap.lines
        runners[n.node] = worker._Runner(winit, script, checks, state, describe, store, pid)
    conn.send(("ready", lines))
    while True:
        try:
            msg = conn.recv()
        except EOFError:
            return
        if msg is None:
            return
        r = runners[msg.node]
        r.init = replace(r.init, run_id=msg.run_id, run_dir=msg.run_dir)
        conn.send(("batch", r.run_doc(msg.task)))


class _Child:
    """Parent-side handle of the warm child."""

    def __init__(self, mp: Any, init: _ChildInit) -> None:
        self.conn, child = mp.Pipe(duplex=True)
        self.proc = mp.Process(target=_child_main, args=(child, init), name="ingest-single-doc")
        self.proc.start()
        child.close()
        self.ready = False

    def recv(self, deadline: float | None, stopped: threading.Event) -> tuple[Any, ...] | None:
        """The next message; None when the child exited. Raises TimeoutError past `deadline`
        and _Closed when `stopped` is set."""
        while True:
            if stopped.is_set():
                raise _Closed
            try:
                if self.conn.poll():
                    return self.conn.recv()
            except (EOFError, OSError):
                return None
            if not self.proc.is_alive():
                return None
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                raise TimeoutError
            wait([self.conn, self.proc.sentinel], min(POLL_S, deadline - now) if deadline is not None else POLL_S)

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


class _Closed(Exception):
    pass


class _DocFailed(Exception):
    def __init__(self, node: str, error_type: str, message: str, tb: str | None = None) -> None:
        super().__init__(message)
        self.node = node
        self.error_type = error_type
        self.message = message
        self.traceback = tb


@dataclass
class _Output:
    sha: str
    kind: str
    metrics: Mapping[str, float] = field(default_factory=dict)


# Runner


class SingleDocRunner:
    """The warm single-doc worker. Thread-safe; owned by the API server."""

    def __init__(
        self,
        config: Config,
        *,
        dag: str = "default",
        node: str = "embed",
        online: bool = False,
        log_level: str = "INFO",
    ) -> None:
        self.config = config
        self.dag = dag
        self.node = node
        self.online = online
        self.log_level = log_level
        self._mp = multiprocessing.get_context("spawn")
        self._cond = threading.Condition()
        self._queue: deque[tuple[int, Doc]] = deque()
        self._chain: _Chain | None = None
        self._next_chain: _Chain | None = None
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self._catalog: Catalog | None = None
        self._child: _Child | None = None
        """Touched only by the runner thread."""

    def start(self) -> None:
        """Resolve the lineage and start the child process (loads all models once)."""
        with self._cond:
            if self._thread is not None:
                raise RuntimeError("runner already started")
            catalog = Catalog(self.config.catalog_path)
            try:
                self._chain = _resolve(self.config, catalog, self.dag, self.node)
            except BaseException:
                catalog.close()
                raise
            self._catalog = catalog
            self._thread = threading.Thread(target=self._loop, name="ingest-single-doc", daemon=True)
            self._thread.start()

    def submit(self, path: Path) -> int:
        """Queue a PDF that lives in a source folder (e.g. config.uploads_dir / "x.pdf"); returns
        the run id immediately. Progress is on catalog.events(run_id)."""
        doc = self._doc(Path(path))
        with self._cond:
            if self._catalog is None or self._stopped.is_set():
                raise RuntimeError("runner is not running")
            chain = self._next_chain or self._chain
            assert chain is not None
            catalog = self._catalog
            with catalog.transaction():
                catalog.restore_removed_doc(doc.id)
                run_id = catalog.create_run(
                    self.dag,
                    {"path": str(doc.file), "doc_id": doc.id, "node": self.node},
                    scratch=False,
                    commit=chain.top.commit,
                    dirty=False,
                    kind="single_doc",
                )
                catalog.add_event(run_id, "queued", {"doc_id": doc.id, "path": doc.paths[0]})
            self._queue.append((run_id, doc))
            self._cond.notify_all()
        return run_id

    def reload(self) -> None:
        """Re-resolve the lineage after a promotion and restart the child with the new chain."""
        with self._cond:
            if self._catalog is None or self._stopped.is_set():
                raise RuntimeError("runner is not running")
            chain = _resolve(self.config, self._catalog, self.dag, self.node)
            latest = self._next_chain or self._chain
            if latest is not None and latest.nodes == chain.nodes:
                chain.cleanup()
                return
            if self._next_chain is not None:
                self._next_chain.cleanup()
            self._next_chain = chain
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._stopped.set()
            self._cond.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join()
        with self._cond:
            for chain in (self._chain, self._next_chain):
                if chain is not None:
                    chain.cleanup()
            self._chain = self._next_chain = None
            if self._catalog is not None:
                self._catalog.close()
                self._catalog = None

    # Submission

    def _doc(self, path: Path) -> Doc:
        """contract.Doc for a file in a folder of the DAG's source."""
        source = self.config.sources[self._source()]
        file = path.resolve()
        if not file.is_file():
            raise FileNotFoundError(f"{path} is not a file")
        rel = None
        for i, folder in enumerate(source.folders):
            root = folder.resolve()
            if file.is_relative_to(root):
                inner = file.relative_to(root).as_posix()
                rel = inner if i == 0 else f"{folder.name}/{inner}"
                break
        if rel is None:
            raise ValueError(f"{path} is not in a folder of source {source.name!r}")
        flags, _ = check_pdf(file)
        return Doc(
            id=sha256_file(file),
            paths=(rel,),
            size=file.stat().st_size,
            file=file,
            meta=meta_for(source, rel),
            flags=flags,
        )

    def _source(self) -> str:
        with self._cond:
            chain, catalog = self._chain, self._catalog
            if chain is None or catalog is None:
                raise RuntimeError("runner is not running")
            return catalog.get_snapshot(catalog.get_version(chain.top.version_id).snapshot_id).source

    # Runner thread (owns the child and its own catalog connection)

    def _loop(self) -> None:
        catalog = Catalog(self.config.catalog_path)
        with self._cond:
            chain = self._chain
        assert chain is not None
        self._child = self._spawn(chain)
        try:
            while True:
                with self._cond:
                    while not self._queue and self._next_chain is None and not self._stopped.is_set():
                        self._cond.wait()
                    if self._stopped.is_set():
                        break
                    if self._next_chain is not None:
                        old, chain = chain, self._next_chain
                        self._chain, self._next_chain = chain, None
                        if self._child is not None:
                            self._child.stop()
                        old.cleanup()
                        self._child = self._spawn(chain)
                        continue
                    run_id, doc = self._queue.popleft()
                self._process(catalog, chain, run_id, doc)
        except _Closed:
            pass
        finally:
            if self._child is not None:
                self._child.stop()
                self._child = None
            with self._cond:
                queued = list(self._queue)
                self._queue.clear()
            for run_id, doc in queued:
                with catalog.transaction():
                    payload = {"doc_id": doc.id, "error_type": "Cancelled", "message": "runner closed"}
                    catalog.add_event(run_id, "doc_failed", payload)
                    catalog.set_run_status(run_id, "cancelled", "runner closed")
            catalog.close()

    def _spawn(self, chain: _Chain) -> _Child:
        nodes = [
            _ChildNode(n.node, n.version_id, chain.scripts[n.node], chain.checks[n.node], n.params) for n in chain.nodes
        ]
        init = _ChildInit(self.dag, self.config.objects_dir, nodes, not self.online, self.log_level)
        return _Child(self._mp, init)

    def _process(self, catalog: Catalog, chain: _Chain, run_id: int, doc: Doc) -> None:
        """Run one doc through the chain. Raises _Closed when close() interrupts it."""
        runlog = obs.RunLog(self.config.run_dir(run_id), run_id=run_id, dag=self.dag)
        top = chain.top

        def emit(event: str, text: str, *, level: str = "INFO", node: str | None = None, **payload: Any) -> None:
            fields = {k: v for k, v in payload.items() if k != "message"}
            runlog.event(event, level=level, message=text, node=node, doc_id=doc.id, **fields)
            catalog.add_event(run_id, event, {"doc_id": doc.id, **({"node": node} if node else {}), **payload})

        status: RunStatus = "failed"
        error: str | None = None
        try:
            catalog.set_run_status(run_id, "running")
            emit(
                "doc_started",
                f"single-doc run {run_id}: {doc.paths[0]}",
                path=doc.paths[0],
                version_id=top.version_id,
                lineage={n.node: n.version_id for n in chain.nodes},
            )
            outputs: dict[str, _Output] = {}
            for n in chain.nodes:
                entry = catalog.cache_get(n.node_fp, doc.id)
                if entry is not None:
                    self._check_cached(chain, n, entry.object_sha, entry.kind)
                    out, cached = _Output(entry.object_sha, entry.kind, dict(entry.metrics)), True
                else:
                    out, cached = self._run_node(catalog, runlog, chain, n, run_id, doc, outputs), False
                outputs[n.node] = out
                emit("node_finished", f"{n.node}: done", node=n.node, metrics=dict(out.metrics), cached=cached)
            parse = next((outputs[n.node] for n in chain.nodes if chain.artifact_types[n.node] == "parse"), None)
            pages = parse.metrics.get("pages") if parse is not None else None
            with catalog.transaction():
                catalog.add_pending_doc(
                    PendingDocInput(
                        doc_id=doc.id,
                        version_id=top.version_id,
                        run_id=run_id,
                        paths=doc.paths,
                        meta=dict(doc.meta),
                        object_sha=outputs[top.node].sha,
                        pages=int(pages) if pages is not None else None,
                    )
                )
                emit("doc_finished", f"single-doc run {run_id} succeeded")
                catalog.set_run_status(run_id, "succeeded")
            status = "succeeded"
        except _DocFailed as e:
            error = f"{e.node}: {e.error_type}: {e.message}"
            if e.traceback:
                runlog.event("traceback", level="ERROR", node=e.node, doc_id=doc.id, traceback=e.traceback)
            emit("doc_failed", error, level="ERROR", node=e.node, error_type=e.error_type, message=e.message)
        except _Closed:
            status, error = "cancelled", "runner closed"
            emit("doc_failed", error, level="WARNING", error_type="Cancelled", message=error)
            raise
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            runlog.event("engine_error", level="ERROR", message=error, doc_id=doc.id, traceback=traceback.format_exc())
            emit("doc_failed", error, level="ERROR", error_type=type(e).__name__, message=str(e))
        finally:
            if status != "succeeded":
                catalog.set_run_status(run_id, status, error)
            runlog.close()

    def _check_cached(self, chain: _Chain, n: LineageNode, sha: str, kind: str) -> None:
        """Script checks of a cached record (hard checks passed when it was cached)."""
        path = chain.checks[n.node]
        if path is None:
            return
        record = ObjectStore(self.config.objects_dir).get(sha, kind)  # type: ignore[arg-type]
        errors = [i for i in script_checks(load_checks(path), record, dict(n.params)) if i.level == "error"]
        if errors:
            raise _DocFailed(n.node, "CheckFailed", "; ".join(f"{i.code}: {i.message}" for i in errors))

    def _run_node(
        self,
        catalog: Catalog,
        runlog: obs.RunLog,
        chain: _Chain,
        n: LineageNode,
        run_id: int,
        doc: Doc,
        outputs: Mapping[str, _Output],
    ) -> _Output:
        """Run node `n` in the child and cache its output. Raises _DocFailed; a timed-out or
        crashed child is replaced right away, one whose setup failed on the next doc."""
        if self._child is None:
            self._child = self._spawn(chain)
        child = self._child
        if not child.ready:
            self._wait_ready(child, runlog, run_id)
        inputs = {i: worker.InputObject(outputs[i].sha, outputs[i].kind) for i in n.inputs}
        task = _NodeTask(n.node, run_id, self.config.run_dir(run_id), worker.DocTask(doc, inputs, n.version_id))
        try:
            child.conn.send(task)
            msg = child.recv(time.monotonic() + n.timeout, self._stopped)
        except _Closed:
            child.kill()
            self._child = None
            raise
        except TimeoutError:
            child.kill()
            self._child = self._spawn(chain)
            raise _DocFailed(
                n.node, "Timeout", f"no result within the {n.timeout:g} s timeout; worker {child.proc.pid} killed"
            ) from None
        except OSError:
            msg = None
        if msg is None:
            child.kill()
            self._child = self._spawn(chain)
            raise _DocFailed(n.node, "WorkerCrashed", f"worker {child.proc.pid} exited with code {child.proc.exitcode}")
        batch: worker.Batch = msg[1]
        runlog.write_lines(batch.lines)
        r = batch.results[0]
        if r.object_sha is not None and r.kind is not None:
            catalog.cache_put(n.node_fp, doc.id, r.object_sha, r.kind, r.duration_s, r.metrics)
        if r.status != "ok":
            raise _DocFailed(n.node, r.error_type or "Error", r.error_message or "", r.traceback)
        assert r.object_sha is not None and r.kind is not None
        return _Output(r.object_sha, r.kind, dict(r.metrics))

    def _wait_ready(self, child: _Child, runlog: obs.RunLog, run_id: int) -> None:
        """Wait for every setup(); the captured lines go to this run's log. Raises _DocFailed
        when a setup fails or the child dies."""
        try:
            msg = child.recv(None, self._stopped)
        except _Closed:
            child.kill()
            self._child = None
            raise
        if msg is None or msg[0] == "failed":
            child.kill()
            self._child = None
        if msg is None:
            raise _DocFailed(
                "setup", "WorkerCrashed", f"worker {child.proc.pid} exited with code {child.proc.exitcode} during setup"
            )
        lines = msg[2].lines if msg[0] == "failed" else msg[1]
        runlog.write_lines([{**line, "run_id": run_id} for line in lines])
        if msg[0] == "failed":
            f: worker.Failed = msg[2]
            raise _DocFailed(msg[1], f.error_type, f"{f.stage} failed: {f.error_type}: {f.error_message}", f.traceback)
        child.ready = True
