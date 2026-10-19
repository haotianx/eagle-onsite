"""App-wide live events: repo changes for stale and uncommitted badges."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.sse import EventSourceResponse, ServerSentEvent

router = APIRouter(tags=["events"])


@router.get("/events", response_class=EventSourceResponse)
async def events() -> AsyncIterator[ServerSentEvent]:
    """SSE: `repo_changed` {paths: [repo-relative paths]} whenever DAG, node or checks files change
    (gitinfo.watch)."""
    raise NotImplementedError
    yield
