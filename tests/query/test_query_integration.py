"""Uploads and the library against the real ingestion engine (Query-Design §22): a toy default DAG
(page text → hashed vectors, no models) run and promoted over the fixtures, the real
SingleDocRunner and the real library view."""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import subprocess
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from ingest.config import Config, load_config
from ingest.engine.executor import run as run_dag
from ingest.engine.planner import RunRequest
from ingest.query import QueryConfig
from ingest.query.library import LibraryCache
from ingest.query.store import ChatStore
from ingest.query.uploads import Uploads
from ingest.sources import sync
from ingest.state import promote
from ingest.store.catalog import Catalog

NODES = Path(__file__).parent / "nodes"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "proposals"
SZEKERES = FIXTURES / "2292 SZEKERES/CONTRACT/AEROTECH/2292 Szekeres Proposal.pdf"
ADDENDUM = FIXTURES / "3426 AGUILAR COLINA FUERTE/CONTRACT/PROPOSAL ADDENDUM.pdf"
FEE = "1,750.00"

DAG = """\
name: default
source: proposals
nodes:
  text:
    script: nodes/text.py
    timeout: 60
  embed:
    script: nodes/embed.py
    inputs: [text]
    params: {model: %s}
    workers: 1
    timeout: 60
"""


def _vector() -> Callable[[str], np.ndarray]:
    spec = importlib.util.spec_from_file_location("query_toy_embed", NODES / "embed.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.vector


class HashEmbedder:
    def __init__(self) -> None:
        self.vector = _vector()

    def embed(self, text: str) -> np.ndarray:
        return self.vector(text)


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def write_dag(repo: Path, model: str) -> None:
    (repo / "dags").mkdir(exist_ok=True)
    (repo / "dags" / "default.yaml").write_text(DAG % model)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"embed with {model}")


def run_and_promote(config: Config) -> dict[str, int]:
    result = run_dag(config, RunRequest(dag="default"))
    catalog = Catalog(config.catalog_path)
    try:
        assert result.status == "succeeded", catalog.get_run(result.run_id).error
        for node, version_id in result.versions.items():
            current = catalog.current_version("default", node)
            if current is None or current.id != version_id:
                promote(catalog, config, version_id)
    finally:
        catalog.close()
    return dict(result.versions)


async def until(check: Callable[[], bool], timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.05)


@pytest.fixture
def config(tmp_repo: Path) -> Config:
    (tmp_repo / "ingest.toml").write_text(
        f'data_dir = ".ingest"\n\n[sources.proposals]\nfolders = ["{FIXTURES}", "{{data_dir}}/uploads"]\n'
    )
    shutil.copytree(NODES, tmp_repo / "nodes", ignore=shutil.ignore_patterns("__pycache__"))
    write_dag(tmp_repo, "toy-a")
    config = load_config(tmp_repo)
    catalog = Catalog(config.catalog_path)
    try:
        sync(catalog, config.sources["proposals"])
    finally:
        catalog.close()
    run_and_promote(config)
    return config


def with_services(config: Config, body: Callable[[Uploads, LibraryCache], Awaitable[None]]) -> None:
    store = ChatStore(config.data_dir / "chat.sqlite")
    library = LibraryCache(config, QueryConfig(), embedder_factory=lambda meta: HashEmbedder())
    library.load()

    async def main() -> None:
        uploads = Uploads(config, QueryConfig(), store, library, poll_interval=0.05, promotion_interval=0.2)
        await uploads.start()
        try:
            await body(uploads, library)
        finally:
            await uploads.close()

    try:
        asyncio.run(main())
    finally:
        library.close()
        store.close()


async def snapshots(uploads: Uploads, upload_id: str) -> list[dict[str, Any]]:
    async def collect() -> list[dict[str, Any]]:
        return [s async for s in uploads.events(upload_id)]

    return await asyncio.wait_for(collect(), 60)


def test_upload_is_searchable_without_a_run_and_removal_hides_it(config: Config) -> None:
    data = SZEKERES.read_bytes() + b"\n%an upload\n"

    async def body(uploads: Uploads, library: LibraryCache) -> None:
        row = await uploads.add("Szekeres copy.pdf", data)
        assert (row.status, row.stage) == ("processing", "queued")
        progress = await snapshots(uploads, row.id)
        assert progress[-1]["status"] == "ready", progress
        assert {"queued", "reading", "indexing"} & {s["stage"] for s in progress}

        doc = library.document(row.doc_id)
        assert doc is not None and doc.upload and doc.pending
        assert doc.filename == "Szekeres copy.pdf"
        assert any(FEE in p.text for p in library.search(FEE, doc_id=row.doc_id))
        # Its pages repeat the proposal's, so an all-documents search collapses them into one hit.
        assert max(p.also_in for p in library.search(FEE)) >= 1

        again = await uploads.add("Szekeres copy.pdf", data)
        assert (again.status, again.run_id) == ("ready", None)
        proposal = await uploads.add("PROPOSAL ADDENDUM.pdf", ADDENDUM.read_bytes())
        assert proposal.status == "ready" and not proposal.removable

        await uploads.remove(row.id)
        assert not library.contains(row.doc_id)
        assert library.search(FEE, doc_id=row.doc_id) == []
        assert not (config.uploads_dir / "Szekeres copy.pdf").exists()

    with_services(config, body)

    catalog = Catalog(config.catalog_path)
    try:
        doc_id = next(r.doc_id for r in catalog.pending_docs())
        assert doc_id in catalog.removed_docs()
        result = sync(catalog, config.sources["proposals"])
        assert doc_id not in {d.doc_id for d in catalog.snapshot_docs(result.snapshot_id)}
    finally:
        catalog.close()


def test_promoting_another_model_reprocesses_uploads(config: Config) -> None:
    data = SZEKERES.read_bytes() + b"\n%reprocessed\n"

    async def body(uploads: Uploads, library: LibraryCache) -> None:
        row = await uploads.add("Szekeres copy.pdf", data)
        assert (await snapshots(uploads, row.id))[-1]["status"] == "ready"
        first_version = library.version_id

        # No sync, so the new version is built over the snapshot without the upload.
        write_dag(config.repo_root, "toy-b")
        versions = await asyncio.to_thread(run_and_promote, config)
        await until(lambda: library.version_id == versions["embed"])
        assert library.version_id != first_version
        await until(lambda: uploads.get(row.id).run_id != row.run_id and uploads.get(row.id).status == "ready")
        doc = library.document(row.doc_id)
        assert doc is not None and doc.pending
        assert library.search(FEE, doc_id=row.doc_id)

    with_services(config, body)
