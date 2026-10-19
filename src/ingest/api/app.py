"""FastAPI server for the engineer UI (Ingestion-Design §14–16) and the query app (Query-Design)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ingest.config import Config

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
API_PREFIX = "/api"


def api_router() -> APIRouter:
    """Every JSON and SSE route, under API_PREFIX."""
    from fastapi import APIRouter

    from ingest.api.routes import compare, dags, docs, events, runs, search, sources, versions
    from ingest.query import api as chat

    router = APIRouter(prefix=API_PREFIX)
    for module in (dags, runs, versions, compare, search, sources, docs, events, chat):
        router.include_router(module.router)
    return router


def create_app(config: Config) -> FastAPI:
    raise NotImplementedError


def serve(config: Config, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Run the server (blocking); `ingest ui` calls this."""
    raise NotImplementedError
