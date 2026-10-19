"""Run planning, launching, listing, live events, cancel, logs, metrics."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.sse import EventSourceResponse, ServerSentEvent

from ingest.api.models import LogLine, RunCreated, RunDetail, RunMetricsOut, RunPlanOut, RunRequestIn, RunSummary

router = APIRouter(tags=["runs"])


@router.post("/runs/plan")
def plan_run(request: RunRequestIn) -> RunPlanOut:
    """400 with the PlanError message when the request can't be planned."""
    raise NotImplementedError


@router.post("/runs")
def start_run(request: RunRequestIn) -> RunCreated:
    raise NotImplementedError


@router.get("/runs")
def list_runs(dag: str | None = None, limit: int = 100) -> list[RunSummary]:
    """Newest first; includes single-doc runs."""
    raise NotImplementedError


@router.get("/runs/{run_id}")
def get_run(run_id: int) -> RunDetail:
    raise NotImplementedError


@router.get("/runs/{run_id}/events", response_class=EventSourceResponse)
async def run_events(run_id: int, after: int = 0) -> AsyncIterator[ServerSentEvent]:
    """Catalog events of the run as SSE (event = kind, id = event id, data = RunEventOut), from
    `after` or the Last-Event-ID header; ends once the run is finished and all events are sent."""
    raise NotImplementedError
    yield


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: int) -> RunSummary:
    raise NotImplementedError


@router.get("/runs/{run_id}/logs")
def run_logs(
    run_id: int, node: str | None = None, doc: str | None = None, level: str | None = None, limit: int = 5000
) -> list[LogLine]:
    """Lines of the run's log.jsonl, filtered (`level` is a minimum); the last `limit` lines."""
    raise NotImplementedError


@router.get("/runs/{run_id}/metrics")
def run_metrics(run_id: int) -> RunMetricsOut:
    raise NotImplementedError
