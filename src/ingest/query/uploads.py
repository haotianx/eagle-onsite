"""Uploads (Query-Design §12): checks, storage in config.uploads_dir, the single-doc run queue,
progress, removal, and reprocessing after an embed promotion."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi.sse import ServerSentEvent

from ingest.config import Config
from ingest.query import QuerySettings
from ingest.query.library import LibraryCache
from ingest.query.models import UploadOut
from ingest.query.store import ChatStore
from ingest.store.catalog import Catalog

MAX_BYTES = 50 * 1024 * 1024


class Uploads:
    def __init__(
        self,
        config: Config,
        catalog: Catalog,
        store: ChatStore,
        library: LibraryCache,
        settings: QuerySettings,
        *,
        runner_factory: Callable[..., Any] | None = None,
    ) -> None:
        """`runner_factory(config, dag=, node=)` builds the single-doc runner (default
        engine.single_doc.SingleDocRunner); tests pass a fake."""
        raise NotImplementedError

    def start(self) -> None:
        """Start the warm single-doc worker when the library is ready (otherwise on first need)
        and the watcher that reprocesses uploads after an embed promotion."""
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    async def create(self, filename: str, data: bytes) -> UploadOut:
        """Check (%PDF header, ≤ MAX_BYTES), hash; ready at once when the doc is already in the
        library; otherwise store it as uploads_dir/<name>.pdf (" (2)" on a clash) and queue a
        single-doc run. Failures come back as status 'failed' with a reason."""
        raise NotImplementedError

    def get(self, upload_id: int) -> UploadOut:
        """Raises KeyError."""
        raise NotImplementedError

    def events(self, upload_id: int) -> AsyncIterator[ServerSentEvent]:
        """`progress` events while processing, then `ready` or `failed` (query.models)."""
        raise NotImplementedError

    async def remove(self, upload_id: int) -> None:
        """Add the doc to removed_docs, delete the file, reload the library, return chats scoped to
        it to All documents. Raises KeyError, or PermissionError for a proposals-folder doc."""
        raise NotImplementedError
