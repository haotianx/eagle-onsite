from __future__ import annotations

import asyncio
import threading
import time
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from ingest.api.watch import RepoEvents

if TYPE_CHECKING:
    from pathlib import Path

    from tests.api.conftest import World


def test_events_fan_out(client: TestClient, world: World) -> None:
    repo_events: RepoEvents = client.app.state.repo_events
    bodies: list[str] = []

    def read() -> None:
        bodies.append(client.get("/api/events").text)

    readers = [threading.Thread(target=read) for _ in range(2)]
    for t in readers:
        t.start()
    deadline = time.monotonic() + 5
    while repo_events.subscriber_count < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    repo_events.publish({"nodes/chunk.py", "dags/default.yaml"})
    repo_events.stop()
    for t in readers:
        t.join(5)
    expected = 'event: repo_changed\ndata: {"paths": ["dags/default.yaml", "nodes/chunk.py"]}'
    assert len(bodies) == 2 and all(expected in b for b in bodies)
    assert repo_events.subscriber_count == 0


def test_watcher_callback_reaches_subscribers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ingest import gitinfo

    started = threading.Event()
    callbacks: list[Any] = []

    def watch(repo_root: Path, paths: Any, callback: Any, stop: threading.Event, **_: Any) -> None:
        assert list(paths) == ["dags", "nodes"]
        callbacks.append(callback)
        started.set()
        stop.wait()

    monkeypatch.setattr(gitinfo, "watch", watch)
    events = RepoEvents(tmp_path)

    async def main() -> list[list[str]]:
        events.start()
        started.wait(5)
        received: list[list[str]] = []

        async def consume() -> None:
            async for paths in events.subscribe():
                received.append(paths)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)
        await asyncio.to_thread(callbacks[0], {"nodes/embed.py"})
        await asyncio.sleep(0.01)
        await asyncio.to_thread(events.stop)
        await asyncio.wait_for(task, 5)
        return received

    assert asyncio.run(main()) == [["nodes/embed.py"]]
