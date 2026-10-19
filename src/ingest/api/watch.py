"""Repo watcher fanning DAG and node file changes out to SSE subscribers."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator
from pathlib import Path

from ingest import gitinfo

WATCHED = ("dags", "nodes")

log = logging.getLogger(__name__)

_END = object()


class RepoEvents:
    """Runs gitinfo.watch in a background thread and hands each batch of changed repo-relative
    paths to every subscriber. Thread-safe."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root
        self._subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue[object]]] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._watch, name="repo-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop watching and end every subscription."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self._send(_END)

    def publish(self, paths: set[str]) -> None:
        self._send(sorted(paths))

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    async def subscribe(self) -> AsyncIterator[list[str]]:
        """Changed paths per batch, until stop()."""
        entry = (asyncio.get_running_loop(), asyncio.Queue[object]())
        with self._lock:
            self._subscribers.add(entry)
        try:
            while (item := await entry[1].get()) is not _END:
                yield item  # type: ignore[misc]
        finally:
            with self._lock:
                self._subscribers.discard(entry)

    def _send(self, item: object) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, item)
            except RuntimeError:  # loop closed
                pass

    def _watch(self) -> None:
        try:
            gitinfo.watch(self.repo_root, WATCHED, self.publish, self._stop)
        except Exception:
            log.exception("repo watcher stopped")
