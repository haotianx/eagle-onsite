from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from ingest import state
from ingest.store.catalog import Catalog

if TYPE_CHECKING:
    from tests.api.conftest import World


@pytest.fixture
def states(monkeypatch: pytest.MonkeyPatch, world: World) -> None:
    def node_states(catalog: Catalog, config: object, dag: object) -> dict[str, state.NodeState]:
        return {
            "ocr": state.NodeState("ocr", "fresh", world.ocr, (), (), "fp-ocr", False),
            "chunk": state.NodeState("chunk", "running", world.chunk, (), (world.scratch,), "fp-chunk", False),
            "embed": state.NodeState(
                "embed", "candidate", world.embed, (world.embed2,), (), "fp-embed", False, ("input chunk is not current",)
            ),
        }

    monkeypatch.setattr(state, "node_states", node_states)


def test_list_dags(client: TestClient, world: World, states: None) -> None:
    (world.config.repo_root / "dags" / "broken.yaml").write_text("name: [\n")
    (world.config.repo_root / "dags" / "invalid.yaml").write_text(
        "name: other\nsource: nowhere\nnodes:\n  a:\n    script: nodes/missing.py\n"
    )
    dags = {d["name"]: d for d in client.get("/api/dags").json()}
    assert set(dags) == {"default", "broken", "other"}
    default = dags["default"]
    assert default["states"] == {"fresh": 1, "running": 1, "candidate": 1}
    assert default["node_count"] == 3 and default["source"] == "proposals"
    assert default["errors"] == [] and default["dirty"] is False
    assert default["last_run"]["id"] == world.active_run_id
    assert dags["broken"]["errors"] and dags["broken"]["node_count"] == 0 and dags["broken"]["dirty"] is True
    assert dags["other"]["errors"] and dags["other"]["states"] == {}


def test_dirty_from_node_files(client: TestClient, world: World, states: None) -> None:
    (world.config.repo_root / "nodes" / "checks" / "chunk.py").write_text("def validate(record, params):\n    return []\n")
    assert client.get("/api/dags").json()[0]["dirty"] is True
    nodes = {n["name"]: n for n in client.get("/api/dags/default").json()["nodes"]}
    assert nodes["chunk"]["dirty"] is True and nodes["ocr"]["dirty"] is False


def test_get_dag(client: TestClient, world: World, states: None) -> None:
    body = client.get("/api/dags/default").json()
    assert [n["name"] for n in body["nodes"]] == ["ocr", "chunk", "embed"]
    ocr, chunk, embed = body["nodes"]
    assert ocr["artifact_type"] == "parse" and chunk["artifact_type"] == "chunk"
    assert ocr["doc_count"] == 3 and ocr["failure_count"] == 1
    assert ocr["current"]["label"] == "default/ocr@v1" and ocr["current"]["current"] is True
    assert chunk["checks"] == "nodes/checks/chunk.py" and chunk["params"] == {"max_tokens": 64}
    assert chunk["scratch"][0]["id"] == world.scratch.id and chunk["scratch"][0]["scratch"] is True
    assert chunk["active_run_id"] == world.active_run_id and ocr["active_run_id"] is None
    assert embed["candidates"][0]["id"] == world.embed2.id and embed["candidates"][0]["current"] is False
    assert embed["reasons"] == ["input chunk is not current"]


def test_get_invalid_dag_lists_errors(client: TestClient, world: World) -> None:
    (world.config.repo_root / "dags" / "broken.yaml").write_text("name: [\n")
    body = client.get("/api/dags/broken").json()
    assert body["errors"] and body["nodes"] == []
    assert client.get("/api/dags/nope").status_code == 404


def test_node_versions(client: TestClient, world: World) -> None:
    versions = client.get("/api/dags/default/nodes/embed/versions").json()
    assert [v["id"] for v in versions] == [world.embed2.id, world.embed.id]
    assert versions[1]["current"] is True and versions[1]["docs"] == 2 and versions[1]["failed"] == 0
    chunk = client.get("/api/dags/default/nodes/chunk/versions").json()
    assert chunk[0]["scratch"] is True
    assert client.get("/api/dags/default/nodes/nope/versions").status_code == 404
    assert client.get("/api/dags/nope/nodes/embed/versions").status_code == 404
