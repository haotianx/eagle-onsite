from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from ingest import state

if TYPE_CHECKING:
    from tests.api.conftest import World


@pytest.fixture(autouse=True)
def promotable(monkeypatch: pytest.MonkeyPatch, world: World) -> None:
    def fn(catalog: Any, version_id: int) -> tuple[bool, list[str]]:
        v = catalog.get_version(version_id)
        if v.scratch:
            return False, ["scratch"]
        if catalog.current_version(v.dag, v.node).id == version_id:
            return False, ["already current"]
        return True, []

    monkeypatch.setattr(state, "promotable", fn)


def test_get_version(client: TestClient, world: World) -> None:
    v = client.get(f"/api/versions/{world.chunk.id}").json()
    assert v["label"] == "default/chunk@v1" and v["current"] is True
    assert v["inputs"][0]["node"] == "ocr" and v["inputs"][0]["current"] is True
    assert v["issues"] == {"error": 1, "warning": 1}
    assert v["summary"]["chunks"] == 3 and v["summary"]["docs"] == 2
    assert v["promotable"] is False and v["not_promotable_reasons"] == ["already current"]
    assert v["replaces"] is None and v["noncurrent_inputs"] == [] and v["checks_changed"] is False

    e2 = client.get(f"/api/versions/{world.embed2.id}").json()
    assert e2["promotable"] is True and e2["replaces"]["id"] == world.embed.id
    assert e2["summary"]["model"] == "fake-model"
    assert client.get("/api/versions/999").status_code == 404


def test_noncurrent_inputs_and_checks_changed(client: TestClient, world: World) -> None:
    world.catalog.set_current("default", "chunk", world.scratch.id)
    (world.config.repo_root / "nodes" / "checks" / "chunk.py").write_text("def validate(record, params):\n    return []\n")
    embed = client.get(f"/api/versions/{world.embed.id}").json()
    assert embed["noncurrent_inputs"] == ["chunk"] and embed["inputs"][0]["current"] is False
    assert embed["checks_changed"] is False
    assert client.get(f"/api/versions/{world.chunk.id}").json()["checks_changed"] is True


def test_version_docs(client: TestClient, world: World) -> None:
    page = client.get(f"/api/versions/{world.ocr.id}/docs").json()
    assert page["total"] == 3
    assert [d["filename"] for d in page["items"]] == [
        "2292 Szekeres Proposal.pdf",
        "Mike Proposal_Elliot Pickle Ball.pdf",
        "PROPOSAL ADDENDUM.pdf",
    ]
    first = page["items"][0]
    assert first["job_number"] == "2292" and first["project"] == "SZEKERES" and first["path"].startswith("2292 SZEKERES/")
    failed = client.get(f"/api/versions/{world.ocr.id}/docs", params={"status": "error"}).json()
    assert failed["total"] == 1 and failed["items"][0]["error_type"] == "ValueError"
    assert client.get(f"/api/versions/{world.ocr.id}/docs", params={"q": "3002"}).json()["total"] == 1
    assert client.get(f"/api/versions/{world.ocr.id}/docs", params={"q": "elliot"}).json()["total"] == 1
    paged = client.get(f"/api/versions/{world.ocr.id}/docs", params={"offset": 1, "limit": 1}).json()
    assert paged["total"] == 3 and paged["items"][0]["filename"] == "Mike Proposal_Elliot Pickle Ball.pdf"
    chunk = client.get(f"/api/versions/{world.chunk.id}/docs").json()["items"]
    assert chunk[0]["issues"] == {"error": 0, "warning": 1} and chunk[1]["issues"] == {"error": 1, "warning": 0}
    assert client.get("/api/versions/999/docs").status_code == 404


def test_version_doc(client: TestClient, world: World) -> None:
    doc = world.docs["2292 Szekeres Proposal.pdf"]
    ocr = client.get(f"/api/versions/{world.ocr.id}/docs/{doc}").json()
    assert ocr["kind"] == "json" and ocr["record"] == {"pages": [{"page": 1}]} and ocr["omitted_columns"] == []
    embed = client.get(f"/api/versions/{world.embed.id}/docs/{doc}").json()
    assert embed["kind"] == "parquet" and embed["omitted_columns"] == ["vector"]
    assert [r["text"] for r in embed["record"]] == ["aerial mapping fee $1,750.00", "terms"]
    assert "vector" not in embed["record"][0] and embed["record"][0]["regions"][0]["page"] == 1
    assert embed["version"]["id"] == world.embed.id and len(embed["paths"]) == 1
    chunk = client.get(f"/api/versions/{world.chunk.id}/docs/{doc}").json()
    assert chunk["issue_list"][0]["code"] == "long" and chunk["issue_list"][0]["filename"] == "2292 Szekeres Proposal.pdf"
    failed = client.get(f"/api/versions/{world.ocr.id}/docs/{world.docs['PROPOSAL ADDENDUM.pdf']}").json()
    assert failed["record"] is None and failed["traceback"] == "Traceback…"
    scratch = client.get(f"/api/versions/{world.scratch.id}/docs/{doc}").json()
    assert scratch["debug_files"] == [{"name": "page.json", "size": scratch["debug_files"][0]["size"]}]
    assert client.get(f"/api/versions/{world.ocr.id}/docs/nope").status_code == 404


def test_issues(client: TestClient, world: World) -> None:
    url = f"/api/versions/{world.chunk.id}/issues"
    assert len(client.get(url).json()) == 2
    assert [i["code"] for i in client.get(url, params={"level": "error"}).json()] == ["shape"]
    assert [i["code"] for i in client.get(url, params={"source": "quality"}).json()] == ["long"]
    elliot = world.docs["Mike Proposal_Elliot Pickle Ball.pdf"]
    assert [i["code"] for i in client.get(url, params={"doc": elliot}).json()] == ["shape"]
    assert client.get("/api/versions/999/issues").status_code == 404


def test_revalidate(client: TestClient, world: World) -> None:
    assert client.post(f"/api/versions/{world.chunk.id}/revalidate").json() == {"issues": 1}
    script = [i for i in client.get(f"/api/versions/{world.chunk.id}/issues").json() if i["source"] == "script"]
    assert [i["code"] for i in script] == ["short"]
    assert client.get(f"/api/versions/{world.chunk.id}").json()["checks_changed"] is False
    assert client.post(f"/api/versions/{world.ocr.id}/revalidate").json() == {"issues": 0}
    assert client.post("/api/versions/999/revalidate").status_code == 404


def test_promote(client: TestClient, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    def promote(catalog: Any, config: Any, version_id: int) -> list[str]:
        ok, reasons = state.promotable(catalog, version_id)
        if not ok:
            raise ValueError(reasons[0])
        v = catalog.get_version(version_id)
        catalog.set_current(v.dag, v.node, version_id)
        return ["2 warnings"]

    monkeypatch.setattr(state, "promote", promote)
    r = client.post(f"/api/versions/{world.embed2.id}/promote").json()
    assert r["version"]["current"] is True and r["replaced"]["id"] == world.embed.id and r["warnings"] == ["2 warnings"]
    r = client.post(f"/api/versions/{world.scratch.id}/promote")
    assert r.status_code == 409 and r.json() == {"detail": "scratch"}
    assert client.post("/api/versions/999/promote").status_code == 404


def test_debug_files(client: TestClient, world: World) -> None:
    doc = world.docs["2292 Szekeres Proposal.pdf"]
    base = f"/api/versions/{world.scratch.id}/docs/{doc}/debug"
    assert [f["name"] for f in client.get(base).json()] == ["page.json"]
    assert client.get(f"{base}/page.json").json() == {"a": 1}
    assert client.get(f"{base}/..%2Flog.jsonl").status_code == 404
    assert client.get(f"{base}/nope").status_code == 404
    assert client.get(f"/api/versions/{world.ocr.id}/docs/{doc}/debug").json() == []
    assert client.get(f"/api/versions/999/docs/{doc}/debug").status_code == 404
