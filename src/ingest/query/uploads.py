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

import asyncio
import logging
import os
import tempfile
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium

from ingest import sources
from ingest.config import Config
from ingest.engine import single_doc
from ingest.engine.single_doc import SingleDocRunner
from ingest.fingerprint import sha256_bytes
from ingest.query import QueryConfig
from ingest.query.library import LibraryCache
from ingest.query.store import ChatStore, UploadRow
from ingest.store.catalog import Catalog

MAX_BYTES = 50 * 1024 * 1024

log = logging.getLogger(__name__)

_FLAG_REASONS = {"encrypted": "Encrypted", "empty": "No pages", "corrupt": "Unreadable"}
_ENDED = ("succeeded", "failed", "cancelled")


class UploadError(Exception):
    """A request the uploads service refuses (e.g. removing a proposals-folder doc)."""


def _basename(filename: str) -> str:
    return filename.replace("\\", "/").rsplit("/", 1)[-1].strip() or "upload.pdf"


def _inspect(path: Path) -> tuple[str | None, int | None]:
    """(failure reason, page count) of a PDF on disk."""
    flags, _ = sources.check_pdf(path)
    for flag in flags:
        if flag in _FLAG_REASONS:
            return _FLAG_REASONS[flag], None
    pdf = pdfium.PdfDocument(path)
    try:
        return None, len(pdf)
    finally:
        pdf.close()


def _failure_reason(payload: Mapping[str, Any]) -> str:
    text = f"{payload.get('error_type') or ''} {payload.get('message') or ''}".lower()
    if "password" in text or "encrypt" in text:
        return "Encrypted"
    if "no pages" in text or "0 pages" in text or "empty" in text:
        return "No pages"
    return "Unreadable"


def _snapshot(row: UploadRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "doc_id": row.doc_id,
        "status": row.status,
        "stage": row.stage,
        "pages": row.pages,
        "error": row.error,
    }


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
        self.config = config
        self.qconfig = qconfig
        self.store = store
        self.library = library
        self._runner_factory = runner_factory
        self._poll_interval = poll_interval
        self._promotion_interval = promotion_interval
        self._catalog: Catalog | None = None
        self._runner: SingleDocRunner | None = None
        self._version_id: int | None = None
        self._lock = asyncio.Lock()
        self._changed = asyncio.Condition()
        self._followers: dict[int, asyncio.Task[None]] = {}
        self._background: set[asyncio.Task[Any]] = set()
        self._watcher: asyncio.Task[None] | None = None

    @property
    def catalog(self) -> Catalog:
        if self._catalog is None:
            self._catalog = Catalog(self.config.catalog_path)
        return self._catalog

    async def start(self) -> None:
        """Start the single-doc worker when the library has a current version, resubmit upload
        rows left 'processing' by a previous server, resubmit docs_to_reprocess, and start the
        promotion watcher (every `promotion_interval` s: when the ref's current version changes,
        reload the worker and the library and resubmit docs_to_reprocess)."""
        current = await asyncio.to_thread(self.catalog.current_version, self.qconfig.dag, self.qconfig.node)
        self._version_id = current.id if current else None
        if current is not None:
            await self._ensure_runner()
        async with self._lock:
            resubmitted = await self._resume_leftovers()
            await self._reprocess(skip=resubmitted)
        self._watcher = asyncio.create_task(self._watch_promotions())

    async def close(self) -> None:
        """Stop the watcher and progress tasks and close the worker."""
        tasks = [*self._followers.values(), *self._background]
        if self._watcher is not None:
            tasks.append(self._watcher)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._followers.clear()
        self._background.clear()
        self._watcher = None
        if self._runner is not None:
            runner, self._runner = self._runner, None
            await asyncio.to_thread(runner.close)
        if self._catalog is not None:
            self._catalog.close()
            self._catalog = None

    async def add(self, filename: str, data: bytes) -> UploadRow:
        """Check and register an uploaded file; returns the card row.
        - Not "%PDF" -> failed "Not a PDF"; over MAX_BYTES -> failed "Over 50 MB"; encrypted,
          unreadable or 0 pages (sources.check_pdf) -> failed with that reason. Failed files
          are not stored.
        - Already in the library -> ready immediately (removable only if it is an upload).
        - Already processing (another row for the same doc) -> processing, following that run.
        - Otherwise stored as config.uploads_dir/<name>.pdf (" (2)" etc. on a name clash),
          un-removed if it had been removed, submitted to the worker -> processing."""
        name = _basename(filename)
        doc_id = await asyncio.to_thread(sha256_bytes, data)
        if b"%PDF" not in data[:1024]:
            return await self._failed(doc_id, name, "Not a PDF")
        if len(data) > MAX_BYTES:
            return await self._failed(doc_id, name, "Over 50 MB")
        tmp = await asyncio.to_thread(self._write_temp, data)
        try:
            reason, pages = await asyncio.to_thread(_inspect, tmp)
            if reason is not None:
                return await self._failed(doc_id, name, reason)
            async with self._lock:
                return await self._register(doc_id, name, pages, tmp)
        finally:
            tmp.unlink(missing_ok=True)

    def get(self, upload_id: str) -> UploadRow:
        """Raises KeyError when missing."""
        return self.store.get_upload(upload_id)

    async def events(self, upload_id: str) -> AsyncIterator[dict[str, Any]]:
        """Status snapshots ({id, doc_id, status, stage, pages, error}) from the current one
        until ready or failed; yields once and ends when already settled. Raises KeyError when
        missing."""
        row = self.store.get_upload(upload_id)
        last = _snapshot(row)
        yield last
        while row.status == "processing" and not row.removed:
            async with self._changed:
                while True:
                    row = self.store.get_upload(upload_id)
                    if _snapshot(row) != last or row.removed:
                        break
                    await self._changed.wait()
            if _snapshot(row) != last:
                last = _snapshot(row)
                yield last

    async def remove(self, upload_id: str) -> UploadRow:
        """Remove the upload's doc from the library: hide it in the library immediately, add it
        to the catalog's removed docs, delete its stored file, mark its rows removed and clear
        the scope of chats scoped to it. Raises UploadError for a proposals-folder doc and
        KeyError when missing."""
        async with self._lock:
            row = self.store.get_upload(upload_id)
            if not row.removable:
                raise UploadError("Only uploads can be removed from the library")
            doc_id = row.doc_id
            self.library.hide(doc_id)
            await asyncio.to_thread(self.catalog.add_removed_doc, doc_id)
            paths = {r.path for r in self.store.uploads_for_doc(doc_id) if r.path}
            for path in paths:
                await asyncio.to_thread((self.config.uploads_dir / path).unlink, missing_ok=True)
            self.store.set_doc_removed(doc_id, True)
            self.store.clear_scope(doc_id)
            await self._notify()
            self._spawn(self._reload_library())
            return self.store.get_upload(upload_id)

    # Registering

    def _write_temp(self, data: bytes) -> Path:
        """A temp file in the uploads folder (renamed into place when stored); its suffix keeps
        it out of source scans."""
        folder = self.config.uploads_dir
        folder.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=folder, prefix=".upload-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise
        return Path(name)

    def _target_name(self, name: str) -> str:
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        stem, suffix = name[:-4], name[-4:]
        candidate, n = name, 1
        while (self.config.uploads_dir / candidate).exists():
            n += 1
            candidate = f"{stem} ({n}){suffix}"
        return candidate

    async def _failed(self, doc_id: str, name: str, reason: str) -> UploadRow:
        row = self.store.add_upload(doc_id=doc_id, filename=name, status="failed", removable=True, error=reason)
        await self._notify()
        return row

    async def _register(self, doc_id: str, name: str, pages: int | None, tmp: Path) -> UploadRow:
        if self.library.contains(doc_id):
            info = self.library.document(doc_id)
            row = self.store.add_upload(
                doc_id=doc_id,
                filename=name,
                status="ready",
                removable=bool(info and info.upload),
                pages=pages,
            )
            await self._notify()
            return row
        rows = self.store.uploads_for_doc(doc_id)
        active = [r for r in rows if r.status == "processing" and not r.removed]
        if active:
            row = self.store.add_upload(
                doc_id=doc_id,
                filename=name,
                status="processing",
                removable=True,
                pages=pages,
                stage=active[0].stage,
                run_id=active[0].run_id,
            )
            await self._notify()
            return row
        runner = await self._ensure_runner()
        if runner is None:
            return await self._failed(doc_id, name, "Library isn't ready")
        if any(r.removed for r in rows):
            await asyncio.to_thread(self.catalog.restore_doc, doc_id)
            self.library.unhide(doc_id)
            self.store.set_doc_removed(doc_id, False)
        existing = self._stored_file(doc_id)
        stored = existing or self._target_name(name)
        if existing is None:
            await asyncio.to_thread(os.replace, tmp, self.config.uploads_dir / stored)
        run_id = await asyncio.to_thread(runner.submit, self.config.uploads_dir / stored)
        row = self.store.add_upload(
            doc_id=doc_id,
            filename=name,
            status="processing",
            removable=True,
            path=None if existing else stored,
            pages=pages,
            stage="queued",
            run_id=run_id,
        )
        self._follow(run_id, doc_id)
        await self._notify()
        return row

    # Worker

    async def _ensure_runner(self) -> SingleDocRunner | None:
        if self._runner is not None:
            return self._runner
        current = await asyncio.to_thread(self.catalog.current_version, self.qconfig.dag, self.qconfig.node)
        if current is None:
            return None
        try:
            runner = self._runner_factory(self.config, dag=self.qconfig.dag, node=self.qconfig.node)
            await asyncio.to_thread(runner.start)
        except LookupError:
            log.warning("single-doc worker not started: no current version of %s", self.qconfig.ref)
            return None
        self._runner = runner
        return runner

    def _stored_file(self, doc_id: str) -> str | None:
        for r in self.store.uploads_for_doc(doc_id):
            if r.path and not r.removed and (self.config.uploads_dir / r.path).is_file():
                return r.path
        return None

    async def _resubmit(self, doc_id: str, path: str, rows: list[UploadRow]) -> None:
        assert self._runner is not None
        run_id = await asyncio.to_thread(self._runner.submit, self.config.uploads_dir / path)
        for r in rows:
            self.store.update_upload(r.id, status="processing", stage="queued", error=None, run_id=run_id)
        self._follow(run_id, doc_id)
        await self._notify()

    async def _resume_leftovers(self) -> set[str]:
        """Rows left 'processing' by a previous server process: resubmit their stored files."""
        by_doc: dict[str, list[UploadRow]] = {}
        for r in self.store.processing_uploads():
            by_doc.setdefault(r.doc_id, []).append(r)
        resubmitted = set()
        for doc_id, rows in by_doc.items():
            path = self._stored_file(doc_id)
            if self.library.contains(doc_id):
                await self._settle(rows, status="ready")
            elif self._runner is None:
                await self._settle(rows, status="failed", error="Library isn't ready")
            elif path is None:
                await self._settle(rows, status="failed", error="Unreadable")
            else:
                await self._resubmit(doc_id, path, rows)
                resubmitted.add(doc_id)
        return resubmitted

    async def _reprocess(self, skip: set[str] = frozenset()) -> None:
        """Resubmit upload docs left out of the current version (after a promotion)."""
        if self._runner is None:
            return
        doc_ids = await asyncio.to_thread(
            single_doc.docs_to_reprocess, self.catalog, self.config, self.qconfig.dag, self.qconfig.node
        )
        for doc_id in doc_ids:
            if doc_id in skip:
                continue
            path = self._stored_file(doc_id)
            if path is None:
                continue
            rows = [
                r for r in self.store.uploads_for_doc(doc_id) if not r.removed and r.status in ("ready", "processing")
            ]
            await self._resubmit(doc_id, path, rows)

    async def _watch_promotions(self) -> None:
        while True:
            await asyncio.sleep(self._promotion_interval)
            try:
                current = await asyncio.to_thread(self.catalog.current_version, self.qconfig.dag, self.qconfig.node)
                if current is None or current.id == self._version_id:
                    continue
                self._version_id = current.id
                async with self._lock:
                    if self._runner is not None:
                        await asyncio.to_thread(self._runner.reload)
                    else:
                        await self._ensure_runner()
                    await asyncio.to_thread(self.library.reload_if_stale)
                    await self._reprocess()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("handling a promotion of %s failed", self.qconfig.ref)

    # Progress

    def _follow(self, run_id: int, doc_id: str) -> None:
        if run_id not in self._followers:
            task = asyncio.create_task(self._follow_run(run_id, doc_id))
            self._followers[run_id] = task
            task.add_done_callback(lambda _: self._followers.pop(run_id, None))

    def _run_rows(self, run_id: int, doc_id: str) -> list[UploadRow]:
        return [
            r
            for r in self.store.uploads_for_doc(doc_id)
            if r.run_id == run_id and r.status == "processing" and not r.removed
        ]

    async def _settle(self, rows: list[UploadRow], **changes: Any) -> None:
        for r in rows:
            self.store.update_upload(r.id, **changes)
        if rows:
            await self._notify()

    async def _follow_run(self, run_id: int, doc_id: str) -> None:
        after = 0
        try:
            while True:
                run = await asyncio.to_thread(self.catalog.get_run, run_id)
                for event in await asyncio.to_thread(self.catalog.events, run_id, after):
                    after = event.id
                    if event.kind == "doc_started":
                        await self._settle(self._run_rows(run_id, doc_id), stage="reading")
                    elif event.kind == "node_finished":
                        rows = [r for r in self._run_rows(run_id, doc_id) if r.stage != "indexing"]
                        await self._settle(rows, stage="indexing")
                    elif event.kind == "doc_finished":
                        await self._finish(run_id, doc_id)
                        return
                    elif event.kind == "doc_failed":
                        await self._fail(run_id, doc_id, _failure_reason(event.payload))
                        return
                if run.status == "succeeded":
                    await self._finish(run_id, doc_id)
                    return
                if run.status in _ENDED:
                    await self._fail(run_id, doc_id, "Unreadable")
                    return
                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("following single-doc run %s failed", run_id)
            await self._settle(self._run_rows(run_id, doc_id), status="failed", stage=None, error="Unreadable")

    async def _finish(self, run_id: int, doc_id: str) -> None:
        if not self._run_rows(run_id, doc_id):
            return
        try:
            await asyncio.to_thread(self.library.load)
        except LookupError:
            await self._settle(self._run_rows(run_id, doc_id), status="failed", stage=None, error="Library isn't ready")
            return
        await self._settle(self._run_rows(run_id, doc_id), status="ready", stage=None, error=None)

    async def _fail(self, run_id: int, doc_id: str, reason: str) -> None:
        await self._settle(self._run_rows(run_id, doc_id), status="failed", stage=None, error=reason)

    # Helpers

    async def _notify(self) -> None:
        async with self._changed:
            self._changed.notify_all()

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _reload_library(self) -> None:
        try:
            await asyncio.to_thread(self.library.load)
        except Exception:
            log.exception("reloading the library after a removal failed")
