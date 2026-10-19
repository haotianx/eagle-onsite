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
- run_corpus nodes run once in a single worker over all docs.
- On completion: coverage check, finalize the version (stats incl. metrics.doc_metrics totals),
  record run metrics. On cancel or crash: abandon building versions; cached outputs remain, so a
  rerun reuses finished docs.
- Only one active run per (dag, node): a new run whose nodes overlap an active run waits as
  'queued'; queued runs with an identical request merge into one.
- Workers run with obs.configure_environment(offline=True) unless `online` is set.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ingest.config import Config
from ingest.engine.planner import RunPlan, RunRequest
from ingest.store.catalog import Catalog, RunStatus


@dataclass(frozen=True)
class RunResult:
    run_id: int
    status: RunStatus
    versions: Mapping[str, int]
    """Node → version id produced."""


def execute(catalog: Catalog, config: Config, plan: RunPlan, *, run_id: int | None = None, online: bool = False) -> RunResult:
    """Run in the current process (which manages the workers). Creates the run row unless
    `run_id` is given. Blocks until finished or cancelled."""
    raise NotImplementedError


def run(config: Config, request: RunRequest, *, online: bool = False) -> RunResult:
    """Plan and execute in the foreground (CLI)."""
    raise NotImplementedError


def submit(config: Config, request: RunRequest, *, online: bool = False) -> int:
    """Create a queued run and start a detached subprocess (`python -m ingest.engine.executor
    <run_id>`) that plans and executes it; returns the run id immediately (API)."""
    raise NotImplementedError


def cancel(catalog: Catalog, run_id: int) -> None:
    """Request cancellation; the executor stops dispatching, kills workers and abandons
    building versions."""
    raise NotImplementedError


def main(argv: list[str] | None = None) -> int:
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
