"""FastAPI server for the engineer UI (Ingestion-Design §14–16) and the query app (Query-Design)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ingest.config import Config

if TYPE_CHECKING:
    from fastapi import FastAPI

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def create_app(config: Config) -> FastAPI:
    raise NotImplementedError


def serve(config: Config, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Run the server (blocking); `ingest ui` calls this."""
    raise NotImplementedError
