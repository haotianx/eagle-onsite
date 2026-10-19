"""Run planning, launching, listing, live events, cancel, logs, metrics."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.sse import EventSourceResponse, ServerSentEvent

from ingest import obs
from ingest.api.deps import CatalogDep, ConfigDep, ObjectsDep, bad_request, get_catalog, run_or_404
from ingest.api.models import (
    LogLine,
    NodeMetrics,
    NodePlanOut,
    PlanInput,
    RunCreated,
    RunDetail,
    RunEventOut,
    RunMetricsOut,
    RunPlanOut,
    RunRequestIn,
    RunSummary,
    SlowDoc,
)
from ingest.api.views import TERMINAL, CurrentIds, DocLookup, run_summary, version_detail, version_summary
from ingest.engine import executor, planner
from ingest.store.catalog import Catalog, RunRow

router = APIRouter(tags=["runs"])

POLL_S = 0.4
SLOWEST_PER_NODE = 10


def _request(body: RunRequestIn) -> planner.RunRequest:
    docs = body.docs if isinstance(body.docs, (str, int)) else tuple(body.docs)
    return planner.RunRequest(
        dag=body.dag,
        target=body.target,
        selection=body.selection,
        snapshot_id=body.snapshot_id,
        input_versions=dict(body.input_versions),
        recompute=body.recompute,
        docs=docs,
        code=body.code,
        sample_seed=body.sample_seed,
    )


@router.post("/runs/plan")
def plan_run(body: RunRequestIn, config: ConfigDep, catalog: CatalogDep) -> RunPlanOut:
    """400 with the PlanError message when the request can't be planned."""
    try:
        plan = planner.plan_run(catalog, config, _request(body))
    except planner.PlanError as e:
        raise bad_request(str(e)) from None
    current = CurrentIds(catalog)
    return RunPlanOut(
        code=plan.code,
        commit=plan.commit,
        dirty=plan.dirty,
        snapshot_id=plan.snapshot_id,
        doc_count=len(plan.doc_ids),
        scratch=plan.scratch,
        scratch_reasons=list(plan.scratch_reasons),
        nodes=[
            NodePlanOut(
                node=n.node,
                script=n.script,
                fingerprint=n.fingerprint,
                artifact_type=n.artifact_type,
                inputs=[
                    PlanInput(
                        node=ref.node,
                        version=None
                        if ref.version_id is None
                        else version_summary(catalog.get_version(ref.version_id), current),
                        fingerprint=ref.fingerprint,
                    )
                    for ref in n.inputs.values()
                ],
                docs_total=n.docs_total,
                docs_cached=n.docs_cached,
                docs_to_process=n.docs_to_process,
                docs_blocked=n.docs_blocked,
            )
            for n in plan.nodes
        ],
    )


@router.post("/runs")
def start_run(body: RunRequestIn, config: ConfigDep) -> RunCreated:
    return RunCreated(run_id=executor.submit(config, _request(body)))


@router.get("/runs")
def list_runs(catalog: CatalogDep, dag: str | None = None, limit: int = 100) -> list[RunSummary]:
    """Newest first; includes single-doc runs."""
    return [run_summary(catalog, r) for r in catalog.list_runs(dag, limit)]


@router.get("/runs/{run_id}")
def get_run(run_id: int, config: ConfigDep, catalog: CatalogDep, objects: ObjectsDep) -> RunDetail:
    run = run_or_404(catalog, run_id)
    versions = [v for v in catalog.run_versions(run_id) if v.status == "complete"]
    return RunDetail(
        **run_summary(catalog, run).model_dump(),
        request=dict(run.request),
        versions=[version_detail(config, catalog, objects, v) for v in versions],
    )


def _existing_run(run_id: int, catalog: Annotated[Catalog, Depends(get_catalog)]) -> RunRow:
    return run_or_404(catalog, run_id)


@router.get("/runs/{run_id}/events", response_class=EventSourceResponse)
async def run_events(
    run: Annotated[RunRow, Depends(_existing_run)],
    catalog: CatalogDep,
    request: Request,
    after: int = 0,
) -> AsyncIterator[ServerSentEvent]:
    """Catalog events of the run as SSE (event = kind, id = event id, data = RunEventOut), from
    `after` or the Last-Event-ID header; ends once the run is finished and all events are sent."""
    last_event_id = request.headers.get("last-event-id", "")
    last = int(last_event_id) if last_event_id.isdigit() else after
    ending = False
    while True:
        status = (await run_in_threadpool(catalog.get_run, run.id)).status
        batch = await run_in_threadpool(catalog.events, run.id, last)
        for e in batch:
            last = e.id
            out = RunEventOut(id=e.id, ts=e.ts, kind=e.kind, payload=dict(e.payload))
            yield ServerSentEvent(event=e.kind, id=str(e.id), data=out)
        if batch:
            continue
        if status in TERMINAL:
            if ending:
                return
            ending = True  # one more read for events written right after the status
        await asyncio.sleep(POLL_S)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: int, catalog: CatalogDep) -> RunSummary:
    run_or_404(catalog, run_id)
    executor.cancel(catalog, run_id)
    return run_summary(catalog, catalog.get_run(run_id))


@router.get("/runs/{run_id}/logs")
def run_logs(
    run_id: int,
    config: ConfigDep,
    catalog: CatalogDep,
    node: str | None = None,
    doc: str | None = None,
    level: str | None = None,
    limit: int = 5000,
) -> list[LogLine]:
    """Lines of the run's log.jsonl, filtered (`level` is a minimum); the last `limit` lines."""
    run_or_404(catalog, run_id)
    if level is not None and level.upper() not in obs.LEVELS:
        raise bad_request(f"Unknown log level {level!r}")
    lines = deque(obs.read_logs(config.run_dir(run_id), node=node, doc_id=doc, level=level), maxlen=max(limit, 0))
    return [LogLine(**line) for line in lines]


@router.get("/runs/{run_id}/metrics")
def run_metrics(run_id: int, catalog: CatalogDep) -> RunMetricsOut:
    run = run_or_404(catalog, run_id)
    run_level: dict[str, float] = {}
    nodes: dict[str, dict[str, float]] = {}
    docs: dict[tuple[str, str], dict[str, float]] = {}
    for m in catalog.run_metrics(run_id):
        if m.node is None and m.doc_id is None:
            run_level[m.name] = m.value
        elif m.doc_id is None:
            nodes.setdefault(m.node, {})[m.name] = m.value
        elif m.node is not None:
            docs.setdefault((m.node, m.doc_id), {})[m.name] = m.value

    snapshot_ids = [v.snapshot_id for v in catalog.run_versions(run_id)]
    if isinstance(run.request.get("snapshot_id"), int):
        snapshot_ids.append(run.request["snapshot_id"])
    lookup = DocLookup(catalog, snapshot_ids)
    by_node: dict[str, list[SlowDoc]] = {}
    for (node, doc_id), values in docs.items():
        if "duration_s" not in values:
            continue
        by_node.setdefault(node, []).append(
            SlowDoc(
                node=node,
                doc_id=doc_id,
                filename=lookup.filename(doc_id),
                duration_s=values["duration_s"],
                peak_rss_mb=values.get("peak_rss_mb"),
            )
        )
    slowest = [
        d for items in by_node.values() for d in sorted(items, key=lambda d: -d.duration_s)[:SLOWEST_PER_NODE]
    ]
    return RunMetricsOut(
        run=run_level,
        nodes=[NodeMetrics(node=n, metrics=values) for n, values in nodes.items()],
        slowest=slowest,
    )
