"""FastAPI server for the engineer UI (Ingestion-Design §14–16). The query app (Query-Design), when
installed as `ingest.query.api`, adds its routes and lifecycle through its `mount(app, config)`."""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from ingest.config import Config

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
API_PREFIX = "/api"
QUERY_API = "ingest.query.api"
GRACEFUL_SHUTDOWN_S = 3


def api_router() -> APIRouter:
    """Every JSON and SSE route of the engineer API, under API_PREFIX."""
    from fastapi import APIRouter

    from ingest.api.routes import compare, dags, docs, events, runs, search, sources, versions

    router = APIRouter(prefix=API_PREFIX)
    for module in (dags, runs, versions, compare, search, sources, docs, events):
        router.include_router(module.router)
    return router


def query_api() -> ModuleType | None:
    """The query app's API module, or None when it isn't part of this checkout."""
    try:
        spec = importlib.util.find_spec(QUERY_API)
    except ModuleNotFoundError as e:
        if e.name in ("ingest.query", QUERY_API):
            return None
        raise
    return importlib.import_module(QUERY_API) if spec is not None else None


def create_app(config: Config) -> FastAPI:
    from fastapi import FastAPI

    from ingest.api.watch import RepoEvents
    from ingest.search import IndexCache
    from ingest.store.catalog import Catalog
    from ingest.store.objects import ObjectStore

    catalog = Catalog(config.catalog_path)
    objects = ObjectStore(config.objects_dir)
    repo_events = RepoEvents(config.repo_root)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        repo_events.start()
        try:
            yield
        finally:
            repo_events.stop()
            catalog.close()

    app = FastAPI(
        title="ingest", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=f"{API_PREFIX}/openapi.json"
    )
    app.state.config = config
    app.state.catalog = catalog
    app.state.objects = objects
    app.state.index_cache = IndexCache(catalog, objects)
    app.state.repo_events = repo_events
    app.include_router(api_router())

    if (query := query_api()) is not None:
        query.mount(app, config)

    _mount_web(app, config.repo_root / "web" / "dist")
    return app


def _mount_web(app: FastAPI, dist: Path) -> None:
    """Serve the built web app: /assets, root files, /chat → chat.html, other non-API GETs →
    index.html. Unknown /api paths stay JSON 404s."""
    from fastapi import HTTPException, status
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    @app.api_route(API_PREFIX + "/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    def api_not_found(path: str) -> None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")

    index = dist / "index.html"
    if not index.is_file():
        return
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    chat = dist / "chat.html"
    if chat.is_file():

        @app.get("/chat", include_in_schema=False)
        @app.get("/chat/{path:path}", include_in_schema=False)
        def chat_page(path: str = "") -> FileResponse:
            return FileResponse(chat)

    root = dist.resolve()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        file = (root / path).resolve()
        if path and file.is_relative_to(root) and file.is_file():
            return FileResponse(file)
        return FileResponse(index)


def serve(config: Config, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Run the server (blocking); `ingest ui` calls this. Models were pulled with `ingest models
    pull`, so the server runs offline."""
    import uvicorn

    from ingest import obs

    obs.configure_environment(offline=True)
    uvicorn.run(create_app(config), host=host, port=port, timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_S)
