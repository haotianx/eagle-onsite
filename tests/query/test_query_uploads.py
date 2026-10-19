"""Uploads (Query-Design §12, §22) with a fake single-doc worker and a fake library. Progress is
driven by appending catalog events to the runs the fake worker creates."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import pytest

from ingest import sources
from ingest.config import Config
from ingest.engine import single_doc
from ingest.query import QueryConfig, chat_db_path
from ingest.query.library import DocumentInfo
from ingest.query.store import ChatStore
from ingest.query.uploads import MAX_BYTES, UploadError, Uploads
from ingest.store.catalog import Catalog

FIXTURES = Path(__file__).parents[1] / "fixtures" / "proposals"
ADDENDUM = FIXTURES / "3426 AGUILAR COLINA FUERTE" / "CONTRACT" / "PROPOSAL ADDENDUM.pdf"
ELLIOT = FIXTURES / "3002 ELLIOT" / "CONTRACT" / "Mike Proposal_Elliot Pickle Ball.pdf"


class FakeRunner:
    def __init__(self, config: Config, *, dag: str, node: str) -> None:
        self.catalog = Catalog(config.catalog_path)
        self.dag, self.node = dag, node
        self.submitted: list[Path] = []
        self.starts = self.reloads = self.closes = 0

    def start(self) -> None:
        self.starts += 1

    def submit(self, path: Path) -> int:
        assert path.is_file()
        self.submitted.append(path)
        return self.catalog.create_run(self.dag, {"doc": path.name}, scratch=True, commit=None, dirty=False)

    def reload(self) -> None:
        self.reloads += 1

    def close(self) -> None:
        self.closes += 1
        self.catalog.close()


class FakeLibrary:
    def __init__(self, store: ChatStore) -> None:
        self.store = store
        self.docs: dict[str, DocumentInfo] = {}
        self.pending: set[str] = set()
        self.hidden: set[str] = set()
        self.unhidden: list[str] = []
        self.loads = self.stale_reloads = 0
        self.statuses_at_load: list[list[str]] = []

    @property
    def ready(self) -> bool:
        return True

    def add(self, doc_id: str, *, upload: bool) -> None:
        self.docs[doc_id] = DocumentInfo(doc_id, (), None, None, None, None, 1, upload, False)

    def load(self) -> None:
        self.loads += 1
        self.statuses_at_load.append([r.status for d in self.pending for r in self.store.uploads_for_doc(d)])
        for doc_id in self.pending:
            self.add(doc_id, upload=True)

    def reload_if_stale(self) -> bool:
        self.stale_reloads += 1
        return True

    def hide(self, doc_id: str) -> None:
        self.hidden.add(doc_id)

    def unhide(self, doc_id: str) -> None:
        self.hidden.discard(doc_id)
        self.unhidden.append(doc_id)

    def contains(self, doc_id: str) -> bool:
        return doc_id in self.docs and doc_id not in self.hidden

    def document(self, doc_id: str) -> DocumentInfo | None:
        return None if doc_id in self.hidden else self.docs.get(doc_id)


@dataclass
class Env:
    config: Config
    store: ChatStore
    library: FakeLibrary
    catalog: Catalog
    runners: list[FakeRunner] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)
    reprocess: list[str] = field(default_factory=list)

    def uploads(self, **kw: Any) -> Uploads:
        def factory(config: Config, *, dag: str, node: str) -> FakeRunner:
            runner = FakeRunner(config, dag=dag, node=node)
            self.runners.append(runner)
            return runner

        return Uploads(
            self.config,
            QueryConfig(),
            self.store,
            self.library,  # type: ignore[arg-type]
            runner_factory=factory,  # type: ignore[arg-type]
            poll_interval=0.01,
            promotion_interval=kw.pop("promotion_interval", 0.02),
        )

    @property
    def runner(self) -> FakeRunner:
        return self.runners[-1]

    def promote(self) -> int:
        snapshot = self.catalog.create_snapshot("proposals", [], 0)
        v = self.catalog.create_version(
            dag="default",
            node="embed",
            node_fp=f"fp{snapshot}",
            snapshot_id=snapshot,
            input_versions={},
            run_id=None,
            commit=None,
            dirty=False,
            scratch=False,
            artifact_type=None,
            meta={},
        )
        self.catalog.finalize_version(v.id, {})
        self.catalog.set_current("default", "embed", v.id)
        return v.id

    def stored(self) -> list[str]:
        folder = self.config.uploads_dir
        return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


@pytest.fixture
def env(config: Config, monkeypatch: pytest.MonkeyPatch) -> Any:
    store = ChatStore(chat_db_path(config))
    catalog = Catalog(config.catalog_path)
    e = Env(config, store, FakeLibrary(store), catalog)
    # Phase 1 stubs, implemented later on main.
    monkeypatch.setattr(Catalog, "add_removed_doc", lambda self, doc_id: e.removed.append(doc_id))
    monkeypatch.setattr(Catalog, "restore_doc", lambda self, doc_id: e.restored.append(doc_id), raising=False)
    monkeypatch.setattr(single_doc, "docs_to_reprocess", lambda catalog, config, dag, node: list(e.reprocess))
    yield e
    catalog.close()
    store.close()


def run(coro: Coroutine[Any, Any, Any]) -> Any:
    return asyncio.run(coro)


async def until(pred: Callable[[], bool], timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not pred():
            await asyncio.sleep(0.01)


async def collect(agen: Any) -> list[dict[str, Any]]:
    return [s async for s in agen]


async def finish(env: Env, run_id: int, doc_id: str) -> None:
    env.library.pending.add(doc_id)
    env.catalog.add_event(run_id, "doc_finished", {"doc_id": doc_id})
    env.catalog.set_run_status(run_id, "succeeded")


def pages(path: Path) -> int:
    pdf = pdfium.PdfDocument(path)
    try:
        return len(pdf)
    finally:
        pdf.close()


def test_scanned_addendum_lifecycle(env: Env) -> None:
    data = ADDENDUM.read_bytes()

    async def main() -> None:
        up = env.uploads()
        await up.start()
        row = await up.add("C:\\scans\\PROPOSAL ADDENDUM.pdf", data)
        assert (row.status, row.stage, row.removable) == ("processing", "queued", True)
        assert row.filename == "PROPOSAL ADDENDUM.pdf"
        assert row.path == "PROPOSAL ADDENDUM.pdf"
        assert row.pages == pages(ADDENDUM)
        assert (env.config.uploads_dir / row.path).read_bytes() == data
        assert env.runner.submitted == [env.config.uploads_dir / "PROPOSAL ADDENDUM.pdf"]
        snaps = asyncio.create_task(collect(up.events(row.id)))

        env.catalog.add_event(row.run_id, "queued", {})
        env.catalog.add_event(row.run_id, "doc_started", {"doc_id": row.doc_id})
        await until(lambda: up.get(row.id).stage == "reading")
        env.catalog.add_event(row.run_id, "node_finished", {"node": "parse", "metrics": {"pages": 1}})
        env.catalog.add_event(row.run_id, "node_finished", {"node": "chunk", "metrics": {"chunks": 3}})
        await until(lambda: up.get(row.id).stage == "indexing")
        await finish(env, row.run_id, row.doc_id)

        got = await asyncio.wait_for(snaps, 5)
        assert [(s["status"], s["stage"]) for s in got] == [
            ("processing", "queued"),
            ("processing", "reading"),
            ("processing", "indexing"),
            ("ready", None),
        ]
        assert got[-1] == {
            "id": row.id,
            "doc_id": row.doc_id,
            "status": "ready",
            "stage": None,
            "pages": row.pages,
            "error": None,
        }
        assert env.library.statuses_at_load == [["processing"]]

        again = await up.add("PROPOSAL ADDENDUM.pdf", data)
        assert (again.status, again.removable, again.path, again.run_id) == ("ready", True, None, None)
        assert len(env.runner.submitted) == 1
        assert await collect(up.events(again.id)) == [
            {"id": again.id, "doc_id": row.doc_id, "status": "ready", "stage": None, "pages": row.pages, "error": None}
        ]
        assert env.stored() == ["PROPOSAL ADDENDUM.pdf"]
        await up.close()

    env.promote()
    run(main())


def test_duplicate_while_processing_follows_run(env: Env) -> None:
    async def main() -> None:
        up = env.uploads()
        await up.start()
        first = await up.add("a.pdf", ADDENDUM.read_bytes())
        env.catalog.add_event(first.run_id, "doc_started", {})
        await until(lambda: up.get(first.id).stage == "reading")
        second = await up.add("b.pdf", ADDENDUM.read_bytes())
        assert (second.status, second.stage, second.run_id, second.path) == (
            "processing",
            "reading",
            first.run_id,
            None,
        )
        assert len(env.runner.submitted) == 1
        assert env.stored() == ["a.pdf"]
        await finish(env, first.run_id, first.doc_id)
        await until(lambda: up.get(second.id).status == "ready")
        assert up.get(first.id).status == "ready"
        await up.close()

    env.promote()
    run(main())


def test_check_failures_are_not_stored(env: Env) -> None:
    data = ADDENDUM.read_bytes()

    async def main() -> None:
        up = env.uploads()
        await up.start()
        cases = [
            ("notes.txt", b"hello", "Not a PDF"),
            ("big.pdf", b"%PDF-1.7\n" + b"\0" * MAX_BYTES, "Over 50 MB"),
            ("truncated.pdf", data[: len(data) // 3], "Unreadable"),
        ]
        for name, blob, reason in cases:
            row = await up.add(name, blob)
            assert (row.status, row.error, row.path, row.run_id) == ("failed", reason, None, None)
            assert await collect(up.events(row.id)) == [
                {"id": row.id, "doc_id": row.doc_id, "status": "failed", "stage": None, "pages": None, "error": reason}
            ]
        assert env.stored() == []
        assert env.runner.submitted == []
        await up.close()

    env.promote()
    run(main())


@pytest.mark.parametrize(("flag", "reason"), [("encrypted", "Encrypted"), ("empty", "No pages")])
def test_check_pdf_flags_map_to_reasons(env: Env, monkeypatch: pytest.MonkeyPatch, flag: str, reason: str) -> None:
    monkeypatch.setattr(sources, "check_pdf", lambda path: ((flag,), "x"))

    async def main() -> None:
        up = env.uploads()
        await up.start()
        row = await up.add("x.pdf", ADDENDUM.read_bytes())
        assert (row.status, row.error) == ("failed", reason)
        assert env.stored() == []
        await up.close()

    env.promote()
    run(main())


def test_name_clash_and_suffix(env: Env) -> None:
    async def main() -> None:
        up = env.uploads()
        await up.start()
        a = await up.add("Proposal.pdf", ADDENDUM.read_bytes())
        b = await up.add("Proposal.pdf", ELLIOT.read_bytes())
        c = await up.add(
            "scan",
            (FIXTURES / "3748 GROVE PLAZA" / "CONTRACT" / "Signed Proposal - Grove Plaza Civil.pdf").read_bytes(),
        )
        assert (a.path, b.path, c.path) == ("Proposal.pdf", "Proposal (2).pdf", "scan.pdf")
        assert env.stored() == ["Proposal (2).pdf", "Proposal.pdf", "scan.pdf"]
        await up.close()

    env.promote()
    run(main())


def test_no_current_version_then_worker_starts_lazily(env: Env) -> None:
    async def main() -> None:
        up = env.uploads(promotion_interval=60)
        await up.start()
        assert env.runners == []
        row = await up.add("a.pdf", ADDENDUM.read_bytes())
        assert (row.status, row.error) == ("failed", "Library isn't ready")
        assert env.stored() == []
        env.promote()
        row = await up.add("a.pdf", ADDENDUM.read_bytes())
        assert row.status == "processing"
        assert env.runner.starts == 1
        await up.close()

    run(main())


def test_remove(env: Env) -> None:
    async def main() -> None:
        up = env.uploads()
        await up.start()
        row = await up.add("a.pdf", ADDENDUM.read_bytes())
        await finish(env, row.run_id, row.doc_id)
        await until(lambda: up.get(row.id).status == "ready")
        dup = await up.add("again.pdf", ADDENDUM.read_bytes())
        chat = env.store.create_chat()
        env.store.update_chat(chat.id, scope=row.doc_id)
        loads = env.library.loads

        removed = await up.remove(dup.id)
        assert removed.removed
        assert row.doc_id in env.library.hidden
        assert env.removed == [row.doc_id]
        assert env.stored() == []
        assert all(r.removed for r in env.store.uploads_for_doc(row.doc_id))
        assert env.store.get_chat(chat.id).scope is None
        await until(lambda: env.library.loads == loads + 1)

        with pytest.raises(KeyError):
            await up.remove("missing")
        env.library.add("proposal-doc", upload=False)
        prop = env.store.add_upload(doc_id="proposal-doc", filename="p.pdf", status="ready", removable=False)
        with pytest.raises(UploadError):
            await up.remove(prop.id)
        assert "proposal-doc" not in env.library.hidden
        await up.close()

    env.promote()
    run(main())


def test_proposals_doc_upload_is_not_removable(env: Env) -> None:
    data = ELLIOT.read_bytes()
    doc_id = hashlib.sha256(data).hexdigest()
    env.library.add(doc_id, upload=False)

    async def main() -> None:
        up = env.uploads()
        await up.start()
        row = await up.add("elliot.pdf", data)
        assert (row.status, row.removable) == ("ready", False)
        with pytest.raises(UploadError):
            await up.remove(row.id)
        await up.close()

    env.promote()
    run(main())


def test_reupload_of_removed_doc_restores_it(env: Env) -> None:
    async def main() -> None:
        up = env.uploads()
        await up.start()
        row = await up.add("a.pdf", ADDENDUM.read_bytes())
        await finish(env, row.run_id, row.doc_id)
        await until(lambda: up.get(row.id).status == "ready")
        await up.remove(row.id)
        env.library.docs.pop(row.doc_id)
        env.library.pending.discard(row.doc_id)

        again = await up.add("a.pdf", ADDENDUM.read_bytes())
        assert env.restored == [row.doc_id]
        assert env.library.unhidden == [row.doc_id]
        assert row.doc_id not in env.library.hidden
        assert not any(r.removed for r in env.store.uploads_for_doc(row.doc_id))
        assert (again.status, again.path) == ("processing", "a.pdf")
        assert env.stored() == ["a.pdf"]
        assert len(env.runner.submitted) == 2
        await up.close()

    env.promote()
    run(main())


def test_removed_while_processing_never_turns_ready(env: Env) -> None:
    async def main() -> None:
        up = env.uploads()
        await up.start()
        row = await up.add("a.pdf", ADDENDUM.read_bytes())
        snaps = asyncio.create_task(collect(up.events(row.id)))
        await up.remove(row.id)
        assert [s["status"] for s in await asyncio.wait_for(snaps, 5)] == ["processing"]
        await finish(env, row.run_id, row.doc_id)
        await until(lambda: row.run_id not in up._followers)
        r = up.get(row.id)
        assert (r.status, r.removed) == ("processing", True)
        await up.close()

    env.promote()
    run(main())


def test_run_failures_map_to_reasons(env: Env) -> None:
    async def main() -> None:
        up = env.uploads()
        await up.start()
        a = await up.add("a.pdf", ADDENDUM.read_bytes())
        b = await up.add("b.pdf", ELLIOT.read_bytes())
        env.catalog.add_event(
            a.run_id, "doc_failed", {"node": "parse", "error_type": "PdfiumError", "message": "password required"}
        )
        env.catalog.set_run_status(a.run_id, "failed")
        env.catalog.set_run_status(b.run_id, "failed", "worker crashed")
        await until(lambda: up.get(a.id).status == "failed" and up.get(b.id).status == "failed")
        assert (up.get(a.id).error, up.get(b.id).error) == ("Encrypted", "Unreadable")
        assert up.get(a.id).stage is None
        await up.close()

    env.promote()
    run(main())


def test_promotion_reloads_worker_and_library_and_reprocesses(env: Env) -> None:
    async def main() -> None:
        up = env.uploads()
        await up.start()
        row = await up.add("a.pdf", ADDENDUM.read_bytes())
        await finish(env, row.run_id, row.doc_id)
        await until(lambda: up.get(row.id).status == "ready")
        await asyncio.sleep(0.05)
        assert (env.runner.reloads, env.library.stale_reloads) == (0, 0)

        env.reprocess.append(row.doc_id)
        env.promote()
        await until(lambda: len(env.runner.submitted) == 2)
        assert (env.runner.reloads, env.library.stale_reloads) == (1, 1)
        r = up.get(row.id)
        assert (r.status, r.stage) == ("processing", "queued")
        assert r.run_id != row.run_id
        assert env.runner.submitted[-1] == env.config.uploads_dir / "a.pdf"
        await up.close()

    env.promote()
    run(main())


def test_start_resubmits_rows_left_processing(env: Env) -> None:
    folder = env.config.uploads_dir
    folder.mkdir(parents=True)
    (folder / "left.pdf").write_bytes(ADDENDUM.read_bytes())
    doc_id = hashlib.sha256(ADDENDUM.read_bytes()).hexdigest()
    stale = env.catalog.create_run("default", {}, scratch=True, commit=None, dirty=False)
    left = env.store.add_upload(
        doc_id=doc_id,
        filename="left.pdf",
        status="processing",
        removable=True,
        path="left.pdf",
        stage="reading",
        run_id=stale,
    )
    env.reprocess.append(doc_id)

    async def main() -> None:
        up = env.uploads()
        await up.start()
        assert env.runner.submitted == [folder / "left.pdf"]
        r = up.get(left.id)
        assert (r.status, r.stage) == ("processing", "queued")
        assert r.run_id != stale
        await finish(env, r.run_id, doc_id)
        await until(lambda: up.get(left.id).status == "ready")
        await up.close()

    env.promote()
    run(main())


def test_close_leaves_no_running_tasks(env: Env) -> None:
    async def main() -> None:
        up = env.uploads()
        await up.start()
        await up.add("a.pdf", ADDENDUM.read_bytes())
        await up.close()
        assert env.runner.closes == 1
        assert [t for t in asyncio.all_tasks() if t is not asyncio.current_task()] == []

    env.promote()
    run(main())
