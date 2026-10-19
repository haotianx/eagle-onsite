"""App-wide live events: repo changes for stale and uncommitted badges."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

router = APIRouter(tags=["events"])


@router.get("/events", response_class=EventSourceResponse)
async def events(request: Request) -> AsyncIterator[ServerSentEvent]:
    """SSE: `repo_changed` {paths: [repo-relative paths]} whenever DAG, node or checks files change
    (gitinfo.watch)."""
    async for paths in request.app.state.repo_events.subscribe():
        yield ServerSentEvent(event="repo_changed", data={"paths": paths})
