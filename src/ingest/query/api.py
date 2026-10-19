"""/chat routes + SSE (Query-Design §13) and the query app's startup and shutdown."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ingest.config import Config

if TYPE_CHECKING:
    from fastapi import FastAPI


def mount(app: FastAPI, config: Config) -> None:
    """Add the /chat/* routes and GET /chat (the built web/dist/chat.html), and wrap
    app.router.lifespan_context so the query services (chat store, library cache, uploads and
    the single-doc worker, generation tasks) start after and stop before the app's own
    lifespan. Call before the app starts serving and before any catch-all route."""
    raise NotImplementedError
