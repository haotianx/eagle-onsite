"""Worker process entry point and parent↔worker message protocol (owned by the executor).

A worker is a spawn-start process running `main(conn, init)` over one duplex Pipe:

    worker → parent  ("loaded", Loaded)    script imported, describe() called
                     ("ready", Ready)      setup() done (not sent when init.describe_only)
                     ("failed", Failed)    import, describe() or setup() raised; the worker exits
                     ("batch", Batch)      results of one task
    parent → worker  DocTask | CorpusTask  one task at a time
                     None                  exit

Workers read inputs from and write outputs to the ObjectStore (atomic, idempotent writes) and
run all checks; they never touch the catalog or the run log. Captured output travels back in
the messages and the parent writes it.
"""

from __future__ import annotations

import copy
import json
import os
import time
import traceback
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any, Literal

import pyarrow as pa

from ingest import obs
from ingest.contract import Doc, Issue, NodeContext
from ingest.metrics import doc_metrics
from ingest.scripts import LoadedChecks, LoadedScript, load_checks, load_script
from ingest.store.objects import ObjectStore
from ingest.validation import hard_checks, quality_checks, script_checks

CORPUS_DEBUG_ID = "corpus"


@dataclass(frozen=True)
class WorkerInit:
    run_id: int
    dag: str
    node: str
    run_dir: Path
    objects_dir: Path
    script: Path
    checks: Path | None
    params: Mapping[str, Any]
    scratch: bool
    log_level: str = "INFO"
    offline: bool = True
    describe_only: bool = False


@dataclass(frozen=True)
class InputObject:
    sha: str
    kind: str


@dataclass(frozen=True)
class DocTask:
    doc: Doc
    inputs: Mapping[str, InputObject]
    version_id: int


@dataclass(frozen=True)
class CorpusTask:
    docs: list[Doc]
    inputs: Mapping[str, Mapping[str, InputObject]]
    """Input node → doc id → object."""
    version_id: int


@dataclass(frozen=True)
class Loaded:
    pid: int
    describe: Mapping[str, Any]
    artifact_type: str | None
    lines: list[dict[str, Any]]


@dataclass(frozen=True)
class Ready:
    pid: int
    model_load_s: float
    lines: list[dict[str, Any]]


@dataclass(frozen=True)
class Failed:
    pid: int
    stage: Literal["load", "describe", "setup"]
    error_type: str
    error_message: str
    traceback: str
    lines: list[dict[str, Any]]


@dataclass(frozen=True)
class DocResult:
    doc_id: str
    status: Literal["ok", "error"]
    object_sha: str | None = None
    """Set when a record was written (ok, or failed only script checks)."""
    kind: str | None = None
    metrics: Mapping[str, float] = field(default_factory=dict)
    hard: list[Issue] = field(default_factory=list)
    quality: list[Issue] = field(default_factory=list)
    script: list[Issue] = field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None
    traceback: str | None = None
    duration_s: float | None = None
    peak_rss_mb: float | None = None
    pid: int | None = None


@dataclass(frozen=True)
class Batch:
    results: list[DocResult]
    lines: list[dict[str, Any]]


def main(conn: Connection, init: WorkerInit) -> None:
    obs.configure_environment(offline=init.offline)
    pid = os.getpid()
    stage: Literal["load", "describe", "setup"] = "load"
    cap = obs.Captured()
    try:
        with _capture(init, None, None) as cap:
            script = load_script(init.script)
            checks = load_checks(init.checks) if init.checks is not None else None
            stage = "describe"
            describe = dict(script.describe(copy.deepcopy(dict(init.params)))) if script.describe else {}
            describe = json.loads(json.dumps(describe, default=str))
    except Exception as e:
        conn.send(("failed", _failed(pid, stage, e, cap.lines)))
        return
    conn.send(("loaded", Loaded(pid, describe, script.artifact_type, cap.lines)))
    if init.describe_only:
        return

    try:
        with _capture(init, None, None) as cap:
            start = time.perf_counter()
            state = script.setup(copy.deepcopy(dict(init.params))) if script.setup else None
            model_load_s = time.perf_counter() - start
    except Exception as e:
        conn.send(("failed", _failed(pid, "setup", e, cap.lines)))
        return
    conn.send(("ready", Ready(pid, model_load_s, cap.lines)))

    runner = _Runner(init, script, checks, state, describe, ObjectStore(init.objects_dir), pid)
    while True:
        try:
            task = conn.recv()
        except EOFError:
            return
        if task is None:
            return
        if isinstance(task, DocTask):
            conn.send(("batch", runner.run_doc(task)))
        else:
            conn.send(("batch", runner.run_corpus(task)))


def _capture(init: WorkerInit, version_id: int | None, doc_id: str | None) -> Any:
    return obs.capture(
        run_id=init.run_id, dag=init.dag, node=init.node, version_id=version_id, doc_id=doc_id, level=init.log_level
    )


def _failed(pid: int, stage: Literal["load", "describe", "setup"], e: BaseException, lines: list[dict[str, Any]]) -> Failed:
    return Failed(pid, stage, type(e).__name__, str(e), traceback.format_exc(), lines)


class _Runner:
    def __init__(
        self,
        init: WorkerInit,
        script: LoadedScript,
        checks: LoadedChecks | None,
        state: Any,
        describe: Mapping[str, Any],
        store: ObjectStore,
        pid: int,
    ) -> None:
        self.init = init
        self.script = script
        self.checks = checks
        self.state = state
        self.describe = describe
        self.store = store
        self.pid = pid

    def _ctx(self, params: dict[str, Any], debug_id: str) -> NodeContext:
        init = self.init
        sink = obs.debug_sink(init.run_dir, init.node, debug_id) if init.scratch else None
        return NodeContext(dag=init.dag, node=init.node, params=params, state=self.state, scratch=init.scratch, debug_sink=sink)

    def run_doc(self, task: DocTask) -> Batch:
        doc = task.doc
        params = copy.deepcopy(dict(self.init.params))
        rss = obs.PeakRss()
        start = time.perf_counter()
        error = None
        with _capture(self.init, task.version_id, doc.id) as cap:
            try:
                inputs = {name: self.store.get(o.sha, o.kind) for name, o in task.inputs.items()}
                with rss:
                    record = self.script.run(doc, inputs, params, self._ctx(params, doc.id))
            except Exception as e:
                error = e, traceback.format_exc()
        duration = time.perf_counter() - start
        if error is not None:
            result = self._error(doc.id, error[0], error[1])
        else:
            result = self._finish(doc.id, record, inputs)
        return Batch([_timed(result, duration, rss.peak_mb)], cap.lines)

    def run_corpus(self, task: CorpusTask) -> Batch:
        params = copy.deepcopy(dict(self.init.params))
        rss = obs.PeakRss()
        start = time.perf_counter()
        error = None
        with _capture(self.init, task.version_id, None) as cap:
            try:
                inputs = {
                    name: {d: self.store.get(o.sha, o.kind) for d, o in objs.items()}
                    for name, objs in task.inputs.items()
                }
                with rss:
                    out = self.script.run_corpus(task.docs, inputs, params, self._ctx(params, CORPUS_DEBUG_ID))
                if not isinstance(out, Mapping):
                    raise TypeError(f"run_corpus must return a dict of doc id → records, not {type(out).__name__}")
            except Exception as e:
                error = e, traceback.format_exc()
        duration = (time.perf_counter() - start) / max(len(task.docs), 1)
        results = []
        for doc in task.docs:
            if error is not None:
                result = self._error(doc.id, error[0], error[1])
            elif doc.id not in out:
                result = DocResult(doc.id, "error", error_type="MissingOutput", error_message="run_corpus returned no records for this doc", pid=self.pid)
            else:
                result = self._finish(doc.id, out[doc.id], {n: objs[doc.id] for n, objs in inputs.items()})
            results.append(_timed(result, duration, rss.peak_mb))
        return Batch(results, cap.lines)

    def _error(self, doc_id: str, e: BaseException, tb: str) -> DocResult:
        return DocResult(doc_id, "error", error_type=type(e).__name__, error_message=str(e), traceback=tb, pid=self.pid)

    def _finish(self, doc_id: str, record: Any, inputs: Mapping[str, Any]) -> DocResult:
        """Checks, then write the record unless a hard check failed. A list of dicts is checked
        as the table it is stored as, the form revalidation and downstream nodes see."""
        if isinstance(record, list) and all(isinstance(r, dict) for r in record):
            record = pa.Table.from_pylist(record)
        artifact_type = self.script.artifact_type
        params = copy.deepcopy(dict(self.init.params))
        try:
            hard = hard_checks(artifact_type, record, params, inputs=inputs, meta=self.describe)
            if any(i.level == "error" for i in hard):
                return DocResult(
                    doc_id,
                    "error",
                    hard=hard,
                    error_type="HardCheckFailed",
                    error_message=_summary(hard),
                    pid=self.pid,
                )
            sha, kind = self.store.put(record)
            metrics = doc_metrics(artifact_type, record)
            quality = quality_checks(artifact_type, record, params)
            script = script_checks(self.checks, record, params) if self.checks is not None else []
        except Exception as e:
            return self._error(doc_id, e, traceback.format_exc())
        errors = [i for i in script if i.level == "error"]
        return DocResult(
            doc_id,
            "error" if errors else "ok",
            object_sha=sha,
            kind=kind,
            metrics=metrics,
            hard=hard,
            quality=quality,
            script=script,
            error_type="CheckFailed" if errors else None,
            error_message=_summary(errors) if errors else None,
            pid=self.pid,
        )


def _summary(issues: list[Issue]) -> str:
    return "; ".join(f"{i.code}: {i.message}" for i in issues if i.level == "error")


def _timed(result: DocResult, duration: float, peak_mb: float) -> DocResult:
    return replace(result, duration_s=duration, peak_rss_mb=peak_mb or None)
