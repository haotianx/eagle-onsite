"""App wiring: query app hook, web app serving, lifespan."""

from __future__ import annotations

import importlib.machinery
import sqlite3
import sys
import types
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from ingest.api.app import create_app
from ingest.config import Config


def _config(root: Path) -> Config:
    return Config(repo_root=root, data_dir=root / ".ingest", sources={})


def _dist(root: Path, *, chat: bool = True) -> None:
    dist = root / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("index")
    (dist / "favicon.svg").write_text("icon")
    (dist / "assets" / "app.js").write_text("js")
    if chat:
        (dist / "chat.html").write_text("chat")


def test_web_app_routes(tmp_path: Path) -> None:
    _dist(tmp_path)
    (tmp_path / "secret.txt").write_text("no")
    with TestClient(create_app(_config(tmp_path))) as client:
        assert client.get("/").text == "index"
        assert client.get("/dags/default").text == "index"
        assert client.get("/chat").text == "chat"
        assert client.get("/chat/12").text == "chat"
        assert client.get("/assets/app.js").text == "js"
        assert client.get("/favicon.svg").text == "icon"
        assert client.get("/../secret.txt").text == "index"
        assert client.get("/docs").text == "index"
        for method in ("GET", "POST"):
            r = client.request(method, "/api/nope")
            assert r.status_code == 404
            assert r.json() == {"detail": "Not Found"}
        assert client.get("/api/openapi.json").json()["info"]["title"] == "ingest"


def test_without_chat_page(tmp_path: Path) -> None:
    _dist(tmp_path, chat=False)
    with TestClient(create_app(_config(tmp_path))) as client:
        assert client.get("/chat").text == "index"


def test_without_web_build(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/nope").json() == {"detail": "Not Found"}


def test_query_app_hook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    router = APIRouter(prefix="/chat")

    @router.get("/ping")
    def ping() -> dict[str, str]:
        return {"pong": "yes"}

    @asynccontextmanager
    async def lifespan(app: object, config: Config) -> AsyncIterator[None]:
        assert config.repo_root == tmp_path
        calls.append("start")
        yield
        calls.append("stop")

    package = types.ModuleType("ingest.query")
    package.__spec__ = importlib.machinery.ModuleSpec("ingest.query", None, is_package=True)
    package.__path__ = []
    module = types.ModuleType("ingest.query.api")
    module.__spec__ = importlib.machinery.ModuleSpec("ingest.query.api", None)
    module.router = router
    module.lifespan = lifespan
    monkeypatch.setitem(sys.modules, "ingest.query", package)
    monkeypatch.setitem(sys.modules, "ingest.query.api", module)

    _dist(tmp_path)
    app = create_app(_config(tmp_path))
    with TestClient(app) as client:
        assert calls == ["start"]
        assert client.get("/api/chat/ping").json() == {"pong": "yes"}
        assert client.get("/chat").text == "chat"
    assert calls == ["start", "stop"]


def test_lifespan_closes_catalog(tmp_path: Path) -> None:
    app = create_app(_config(tmp_path))
    with TestClient(app):
        assert app.state.repo_events._thread is not None
    assert app.state.repo_events._thread is None
    with pytest.raises(sqlite3.ProgrammingError):
        app.state.catalog.list_runs()
