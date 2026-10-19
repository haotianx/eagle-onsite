from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pytest
from fastapi.testclient import TestClient

from ingest import search

if TYPE_CHECKING:
    from tests.api.conftest import World


class FakeEmbedder:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def embed(self, query: str) -> np.ndarray:
        self.calls.append(query)
        return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)


@pytest.fixture
def embeds(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    models: list[Any] = []

    def get_embedder(meta: Any) -> FakeEmbedder:
        if not meta.get("model"):
            raise ValueError("version metadata has no embedding model")
        models.append(meta["model"])
        return FakeEmbedder(calls)

    monkeypatch.setattr(search, "get_embedder", get_embedder)
    return calls


def test_compare(client: TestClient, world: World) -> None:
    c = client.get("/api/compare", params={"a": world.chunk.id, "b": world.scratch.id}).json()
    assert c["a"]["id"] == world.chunk.id and c["b"]["scratch"] is True
    assert c["a_summary"]["chunks"] == 3 and c["b_summary"]["chunks"] == 2
    changes = {d["filename"]: d for d in c["changed"]}
    assert changes["2292 Szekeres Proposal.pdf"]["change"] == 2.0
    assert changes["Mike Proposal_Elliot Pickle Ball.pdf"]["b_records"] is None
    assert c["unchanged"] == 0
    r = client.get("/api/compare", params={"a": world.chunk.id, "b": world.embed.id})
    assert r.status_code == 400
    assert client.get("/api/compare", params={"a": world.chunk.id, "b": 999}).status_code == 404


def test_compare_doc(client: TestClient, world: World) -> None:
    doc = world.docs["2292 Szekeres Proposal.pdf"]
    d = client.get(f"/api/compare/docs/{doc}", params={"a": world.embed.id, "b": world.embed2.id}).json()
    assert len(d["a"]) == 2 and len(d["b"]) == 1 and "vector" not in d["a"][0]
    assert d["only_a"] == ["h-terms"] and d["only_b"] == []
    ocr = client.get(f"/api/compare/docs/{doc}", params={"a": world.ocr.id, "b": world.ocr.id}).json()
    assert ocr["a"] == {"pages": [{"page": 1}]} and ocr["only_a"] is None
    elliot = world.docs["Mike Proposal_Elliot Pickle Ball.pdf"]
    one = client.get(f"/api/compare/docs/{elliot}", params={"a": world.embed.id, "b": world.embed2.id}).json()
    assert one["b"] is None and one["only_a"] == ["h-pickle ball courts"]
    assert client.get("/api/compare/docs/nope", params={"a": world.embed.id, "b": world.embed2.id}).status_code == 404
    assert client.get(f"/api/compare/docs/{doc}", params={"a": world.ocr.id, "b": world.embed.id}).status_code == 400


def test_search(client: TestClient, world: World, embeds: list[str]) -> None:
    r = client.post("/api/search", json={"query": "aerial mapping fee", "versions": [world.embed.id, world.embed2.id], "k": 2})
    sides = r.json()["sides"]
    assert embeds == ["aerial mapping fee"]
    a, b = sides
    assert a["version"]["id"] == world.embed.id and a["model"] == "fake-model"
    assert [h["rank"] for h in a["hits"]] == [1, 2]
    top = a["hits"][0]
    assert top["text"] == "aerial mapping fee $1,750.00" and top["score"] == pytest.approx(1.0)
    assert top["filename"] == "2292 Szekeres Proposal.pdf" and top["job_number"] == "2292"
    assert top["regions"] == [{"page": 1, "x0": 1.0, "y0": 2.0, "x1": 3.0, "y1": 4.0}]
    assert top["pages"] == [1] and top["section"] == ["Fees"]
    assert len(b["hits"]) == 1 and b["hits"][0]["score"] == pytest.approx(0.0)


def test_search_errors(client: TestClient, world: World, embeds: list[str]) -> None:
    assert client.post("/api/search", json={"query": "x", "versions": [world.ocr.id]}).status_code == 400
    assert client.post("/api/search", json={"query": "x", "versions": [world.chunk.id]}).status_code == 400
    assert client.post("/api/search", json={"query": "x", "versions": [999]}).status_code == 404
    assert client.post("/api/search", json={"query": "x", "versions": []}).status_code == 422
