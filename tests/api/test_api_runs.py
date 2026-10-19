from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from ingest.api.routes import runs as runs_routes
from ingest.engine import executor, planner
from ingest.spec import DagSpec

if TYPE_CHECKING:
    from tests.api.conftest import World


def _events(text: str) -> list[dict[str, str]]:
    out = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith(":"))
        if fields:
            out.append(fields)
    return out


def test_plan(client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[planner.RunRequest] = []

    def plan_run(catalog: Any, config: Any, request: planner.RunRequest, **_: Any) -> planner.RunPlan:
        seen.append(request)
        node = planner.NodePlan(
            node="embed",
            script="nodes/embed.py",
            fingerprint="fp",
            artifact_type="embed",
            inputs={"chunk": planner.InputRef("chunk", world.chunk.id, "fp-chunk")},
            docs_total=6,
            docs_cached=2,
            docs_to_process=3,
            docs_blocked=1,
        )
        return planner.RunPlan(
            request, DagSpec("default", "proposals", {}, "dags/default.yaml"), "working", "abc", True,
            world.snapshot_id, ("a", "b"), True, ("working tree",), (node,), "env",
        )

    monkeypatch.setattr(planner, "plan_run", plan_run)
    body = {"dag": "default", "target": "embed", "selection": "node", "docs": ["a", "b"]}
    plan = client.post("/api/runs/plan", json=body).json()
    assert seen[0].target == "embed" and tuple(seen[0].docs) == ("a", "b")
    assert plan["doc_count"] == 2 and plan["scratch_reasons"] == ["working tree"]
    assert plan["nodes"][0]["inputs"][0]["version"]["id"] == world.chunk.id
    assert plan["nodes"][0]["docs_to_process"] == 3


def test_plan_error(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def plan_run(*_: Any, **__: Any) -> None:
        raise planner.PlanError("unknown node 'x'")

    monkeypatch.setattr(planner, "plan_run", plan_run)
    r = client.post("/api/runs/plan", json={"dag": "default", "target": "x", "selection": "node"})
    assert r.status_code == 400 and r.json() == {"detail": "unknown node 'x'"}


def test_start_run(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor, "submit", lambda config, request: 42 if request.recompute == "force" else 0)
    assert client.post("/api/runs", json={"dag": "default", "recompute": "force"}).json() == {"run_id": 42}


def test_list_runs(client: TestClient, world: World) -> None:
    runs = client.get("/api/runs").json()
    assert runs[0]["id"] == world.active_run_id
    active = runs[0]
    assert active["status"] == "running" and active["nodes"] == ["chunk", "embed"]
    assert (active["processed"], active["cached"], active["failed"]) == (2, 0, 1)
    assert active["duration_s"] is not None and active["kind"] == "dag"
    done = next(r for r in runs if r["id"] == world.run_id)
    assert (done["processed"], done["cached"], done["failed"]) == (7, 0, 1)
    assert len(client.get("/api/runs", params={"limit": 2}).json()) == 2
    assert client.get("/api/runs", params={"dag": "other"}).json() == []


def test_get_run(client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    from ingest import state

    monkeypatch.setattr(state, "promotable", lambda catalog, vid: (False, ["already current"]))
    run = client.get(f"/api/runs/{world.run_id}").json()
    assert [v["node"] for v in run["versions"]] == ["ocr", "chunk", "embed"]
    assert run["request"]["nodes"] == ["ocr", "chunk", "embed"]
    assert client.get("/api/runs/999").status_code == 404


def test_run_events_drain_and_resume(client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runs_routes, "POLL_S", 0.01)
    events = _events(client.get(f"/api/runs/{world.run_id}/events").text)
    assert [e["event"] for e in events] == ["run_started", "doc_finished", "doc_failed", "doc_cached", "run_finished"]
    ids = [int(e["id"]) for e in events]
    resumed = _events(client.get(f"/api/runs/{world.run_id}/events", params={"after": ids[2]}).text)
    assert [int(e["id"]) for e in resumed] == ids[3:]
    header = _events(client.get(f"/api/runs/{world.run_id}/events", headers={"Last-Event-ID": str(ids[3])}).text)
    assert [int(e["id"]) for e in header] == ids[4:]
    assert '"kind":"run_finished"' in events[-1]["data"].replace(" ", "")
    assert client.get("/api/runs/999/events").status_code == 404


def test_run_events_follow_active_run(client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runs_routes, "POLL_S", 0.01)

    def finish() -> None:
        time.sleep(0.2)
        world.catalog.add_event(world.active_run_id, "run_finished", {"status": "succeeded"})
        world.catalog.set_run_status(world.active_run_id, "succeeded")

    t = threading.Thread(target=finish)
    t.start()
    events = _events(client.get(f"/api/runs/{world.active_run_id}/events").text)
    t.join()
    assert [e["event"] for e in events] == ["doc_finished", "doc_failed", "run_finished"]


def test_cancel(client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(executor, "cancel", lambda catalog, run_id: catalog.request_cancel(run_id))
    assert client.post(f"/api/runs/{world.active_run_id}/cancel").json()["cancel_requested"] is True
    assert client.post("/api/runs/999/cancel").status_code == 404


def test_logs(client: TestClient, world: World) -> None:
    lines = client.get(f"/api/runs/{world.run_id}/logs").json()
    assert [line["event"] for line in lines] == ["run_started", "doc_finished", "doc_failed"]
    doc = world.docs["2292 Szekeres Proposal.pdf"]
    assert [line["message"] for line in client.get(f"/api/runs/{world.run_id}/logs", params={"doc": doc}).json()] == ["ocr done"]
    assert len(client.get(f"/api/runs/{world.run_id}/logs", params={"node": "ocr"}).json()) == 2
    assert [line["level"] for line in client.get(f"/api/runs/{world.run_id}/logs", params={"level": "error"}).json()] == ["ERROR"]
    assert [line["event"] for line in client.get(f"/api/runs/{world.run_id}/logs", params={"limit": 1}).json()] == ["doc_failed"]
    assert client.get(f"/api/runs/{world.run_id}/logs", params={"level": "loud"}).status_code == 400
    assert client.get(f"/api/runs/{world.active_run_id}/logs").json() == []
    assert client.get("/api/runs/999/logs").status_code == 404


def test_metrics(client: TestClient, world: World) -> None:
    m = client.get(f"/api/runs/{world.run_id}/metrics").json()
    assert m["run"] == {"wall_s": 12.0}
    assert m["nodes"] == [{"node": "ocr", "metrics": {"wall_s": 8.0, "docs_processed": 3.0}}]
    assert [(d["filename"], d["duration_s"], d["peak_rss_mb"]) for d in m["slowest"]] == [
        ("Mike Proposal_Elliot Pickle Ball.pdf", 5.0, None),
        ("2292 Szekeres Proposal.pdf", 2.0, 300.0),
    ]
    assert client.get("/api/runs/999/metrics").status_code == 404
