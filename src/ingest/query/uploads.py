"""Uploads (Query-Design §12): checks, storage in the uploads folder, the single-doc run queue,
progress, removal, and reprocessing after a promotion.

Card lifecycle (store.UploadRow):
    processing  stage "queued" -> "reading" (the first lineage node is running) -> "indexing"
                (later nodes) -> ready once the library has reloaded with the doc
    ready       searchable
    failed      `error` holds the reason shown on the card

Failure reasons: "Not a PDF", "Over 50 MB", "Encrypted", "Unreadable", "No pages", "Library
isn't ready" (no current embed version or the worker could not start).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from ingest.config import Config
from ingest.engine.single_doc import SingleDocRunner
from ingest.query import QueryConfig
from ingest.query.library import LibraryCache
from ingest.query.store import ChatStore, UploadRow

MAX_BYTES = 50 * 1024 * 1024


class UploadError(Exception):
    """A request the uploads service refuses (e.g. removing a proposals-folder doc)."""


class Uploads:
    def __init__(
        self,
        config: Config,
        qconfig: QueryConfig,
        store: ChatStore,
        library: LibraryCache,
        *,
        runner_factory: Callable[..., SingleDocRunner] = SingleDocRunner,
        poll_interval: float = 0.5,
        promotion_interval: float = 5.0,
    ) -> None:
        """No I/O. `runner_factory(config, dag=..., node=...)` builds the warm worker."""
        raise NotImplementedError

    async def start(self) -> None:
        """Start the single-doc worker when the library has a current version, resubmit upload
        rows left 'processing' by a previous server, resubmit docs_to_reprocess, and start the
        promotion watcher (every `promotion_interval` s: when the ref's current version changes,
        reload the worker and the library and resubmit docs_to_reprocess)."""
        raise NotImplementedError

    async def close(self) -> None:
        """Stop the watcher and progress tasks and close the worker."""
        raise NotImplementedError

    async def add(self, filename: str, data: bytes) -> UploadRow:
        """Check and register an uploaded file; returns the card row.
        - Not "%PDF" -> failed "Not a PDF"; over MAX_BYTES -> failed "Over 50 MB"; encrypted,
          unreadable or 0 pages (sources.check_pdf) -> failed with that reason. Failed files
          are not stored.
        - Already in the library -> ready immediately (removable only if it is an upload).
        - Already processing (another row for the same doc) -> processing, following that run.
        - Otherwise stored as config.uploads_dir/<name>.pdf (" (2)" etc. on a name clash),
          un-removed if it had been removed, submitted to the worker -> processing."""
        raise NotImplementedError

    def get(self, upload_id: str) -> UploadRow:
        """Raises KeyError when missing."""
        raise NotImplementedError

    async def events(self, upload_id: str) -> AsyncIterator[dict[str, Any]]:
        """Status snapshots ({id, doc_id, status, stage, pages, error}) from the current one
        until ready or failed; yields once and ends when already settled. Raises KeyError when
        missing."""
        raise NotImplementedError
        yield {}

    async def remove(self, upload_id: str) -> UploadRow:
        """Remove the upload's doc from the library: hide it in the library immediately, add it
        to the catalog's removed docs, delete its stored file, mark its rows removed and clear
        the scope of chats scoped to it. Raises UploadError for a proposals-folder doc and
        KeyError when missing."""
        raise NotImplementedError
