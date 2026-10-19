"""A small catalog for API tests: the fixture PDFs synced as a source, a three-node DAG in a git
repo, and synthetic versions, issues, runs, events, metrics and logs."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest
from fastapi.testclient import TestClient

from ingest import gitinfo, obs, sources
from ingest.api.app import create_app
from ingest.config import Config, load_config
from ingest.contract import Issue
from ingest.fingerprint import checks_fingerprint, sha256_file
from ingest.store.catalog import Catalog, VersionDocInput, VersionRow
from ingest.store.objects import ObjectStore

DIM = 4

DAG_YAML = """\
name: default
source: proposals
nodes:
  ocr:
    script: nodes/parse.py
  chunk:
    script: nodes/chunk.py
    inputs: [ocr]
    params: { max_tokens: 64 }
    checks: nodes/checks/chunk.py
  embed:
    script: nodes/embed.py
    inputs: [chunk]
"""

SCRIPTS = {
    "nodes/parse.py": 'ARTIFACT_TYPE = "parse"\n\n\ndef run(doc, inputs, params, ctx):\n    return {}\n',
    "nodes/chunk.py": 'ARTIFACT_TYPE: str = "chunk"\n\n\ndef run(doc, inputs, params, ctx):\n    return []\n',
    "nodes/embed.py": 'ARTIFACT_TYPE = "embed"\n\n\ndef run(doc, inputs, params, ctx):\n    return []\n',
    "nodes/checks/chunk.py": (
        "from ingest.contract import Issue\n\n\n"
        "def validate(record, params):\n"
        '    return [Issue("warning", "short", "short chunk") for r in record.to_pylist() if len(r["text"]) < 10]\n'
    ),
}

REGION = pa.struct([("page", pa.int32()), ("x0", pa.float64()), ("y0", pa.float64()), ("x1", pa.float64()), ("y1", pa.float64())])


@dataclass
class World:
    config: Config
    catalog: Catalog
    objects: ObjectStore
    snapshot_id: int
    docs: dict[str, str]
    """filename → doc id."""
    run_id: int
    active_run_id: int
    ocr: VersionRow
    chunk: VersionRow
    embed: VersionRow
    embed2: VersionRow
    scratch: VersionRow


def chunk_rows(doc_id: str, texts: list[str]) -> list[dict]:
    return [
        {
            "chunk_id": f"{doc_id[:8]}-{i}",
            "doc_id": doc_id,
            "seq": i,
            "text": t,
            "embed_text": t,
            "pages": [1],
            "section": ["Fees"],
            "regions": [{"page": 1, "x0": 1.0, "y0": 2.0, "x1": 3.0, "y1": 4.0}],
            "text_hash": f"h-{t}",
            "tokens": len(t.split()),
        }
        for i, t in enumerate(texts)
    ]


def chunk_table(doc_id: str, texts: list[str]) -> pa.Table:
    schema = pa.schema(
        [
            ("chunk_id", pa.string()),
            ("doc_id", pa.string()),
            ("seq", pa.int32()),
            ("text", pa.string()),
            ("embed_text", pa.string()),
            ("pages", pa.list_(pa.int32())),
            ("section", pa.list_(pa.string())),
            ("regions", pa.list_(REGION)),
            ("text_hash", pa.string()),
            ("tokens", pa.int32()),
        ]
    )
    return pa.Table.from_pylist(chunk_rows(doc_id, texts), schema=schema)


def embed_table(doc_id: str, texts: list[str], vectors: list[list[float]]) -> pa.Table:
    t = chunk_table(doc_id, texts)
    arr = np.asarray(vectors, dtype=np.float32)
    arr /= np.linalg.norm(arr, axis=1, keepdims=True)
    column = pa.FixedSizeListArray.from_arrays(pa.array(arr.reshape(-1)), DIM)
    return t.append_column(pa.field("vector", pa.list_(pa.float32(), DIM)), column)


def _version(
    w_catalog: Catalog,
    objects: ObjectStore,
    *,
    node: str,
    run_id: int,
    snapshot_id: int,
    records: dict[str, object],
    errors: tuple[str, ...] = (),
    inputs: dict[str, int] | None = None,
    artifact_type: str,
    scratch: bool = False,
    meta: dict | None = None,
) -> VersionRow:
    v = w_catalog.create_version(
        dag="default",
        node=node,
        node_fp=f"fp-{node}-{run_id}",
        snapshot_id=snapshot_id,
        input_versions=inputs or {},
        run_id=run_id,
        commit="abc",
        dirty=False,
        scratch=scratch,
        artifact_type=artifact_type,
        meta=meta or {"params": {}},
    )
    rows = []
    for doc_id, record in records.items():
        sha, kind = objects.put(record)
        rows.append(VersionDocInput(doc_id, "ok", sha, kind, duration_s=1.5, peak_rss_mb=100.0, metrics={"records": 1}))
    for doc_id in errors:
        rows.append(VersionDocInput(doc_id, "error", error_type="ValueError", error_message="bad pdf", traceback="Traceback…"))
    w_catalog.add_version_docs(v.id, rows)
    stats = {"docs": len(rows), "ok": len(records), "error": len(errors), "skipped": 0, "processed": len(rows), "cached": 0}
    return w_catalog.finalize_version(v.id, stats)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture(autouse=True)
def no_repo_watch(monkeypatch: pytest.MonkeyPatch) -> None:
    def watch(repo_root: Path, paths: object, callback: object, stop: threading.Event, **_: object) -> None:
        stop.wait()

    monkeypatch.setattr(gitinfo, "watch", watch)


@pytest.fixture
def world(tmp_repo: Path) -> Iterator[World]:
    (tmp_repo / "dags").mkdir()
    (tmp_repo / "dags" / "default.yaml").write_text(DAG_YAML)
    for rel, text in SCRIPTS.items():
        (tmp_repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_repo / rel).write_text(text)
    _git(tmp_repo, "add", "-A")
    _git(tmp_repo, "commit", "-q", "-m", "init")

    config = load_config(tmp_repo)
    catalog = Catalog(config.catalog_path)
    objects = ObjectStore(config.objects_dir)
    snapshot_id = sources.sync(catalog, config.sources["proposals"]).snapshot_id
    docs = {r.meta["filename"]: r.doc_id for r in catalog.snapshot_docs(snapshot_id)}
    szekeres, elliot = docs["2292 Szekeres Proposal.pdf"], docs["Mike Proposal_Elliot Pickle Ball.pdf"]
    scan = docs["PROPOSAL ADDENDUM.pdf"]

    run_id = catalog.create_run("default", {"dag": "default", "nodes": ["ocr", "chunk", "embed"]}, scratch=False, commit="abc", dirty=False)
    catalog.set_run_status(run_id, "running")
    ocr = _version(
        catalog, objects, node="ocr", run_id=run_id, snapshot_id=snapshot_id, artifact_type="parse",
        records={szekeres: {"pages": [{"page": 1}]}, elliot: {"pages": [{"page": 1}, {"page": 2}]}}, errors=(scan,),
    )
    checks_fp = checks_fingerprint(sha256_file(tmp_repo / "nodes/checks/chunk.py"), {"max_tokens": 64})
    chunk = _version(
        catalog, objects, node="chunk", run_id=run_id, snapshot_id=snapshot_id, artifact_type="chunk",
        records={szekeres: chunk_table(szekeres, ["aerial mapping fee $1,750.00", "terms"]), elliot: chunk_table(elliot, ["pickle ball courts"])},
        inputs={"ocr": ocr.id}, meta={"params": {"max_tokens": 64}, "checks_fp": checks_fp},
    )
    describe = {"describe": {"model": "fake-model", "dim": DIM, "metric": "cosine"}, "params": {}}
    embed = _version(
        catalog, objects, node="embed", run_id=run_id, snapshot_id=snapshot_id, artifact_type="embed",
        records={
            szekeres: embed_table(szekeres, ["aerial mapping fee $1,750.00", "terms"], [[1, 0, 0, 0], [0, 1, 0, 0]]),
            elliot: embed_table(elliot, ["pickle ball courts"], [[0, 0, 1, 0]]),
        },
        inputs={"chunk": chunk.id}, meta=describe,
    )
    for v in (ocr, chunk, embed):
        catalog.set_current("default", v.node, v.id)
    catalog.add_issues(chunk.id, "quality", [(szekeres, Issue("warning", "long", "chunk too long", page=1))])
    catalog.add_issues(chunk.id, "hard", [(elliot, Issue("error", "shape", "bad shape"))])
    for kind, payload in [
        ("run_started", {"docs": 6}),
        ("doc_finished", {"node": "ocr", "doc_id": szekeres}),
        ("doc_failed", {"node": "ocr", "doc_id": scan}),
        ("doc_cached", {"node": "chunk", "doc_id": szekeres}),
        ("run_finished", {"status": "succeeded"}),
    ]:
        catalog.add_event(run_id, kind, payload)
    catalog.add_metrics(
        run_id,
        [
            (None, None, "wall_s", 12.0),
            ("ocr", None, "wall_s", 8.0),
            ("ocr", None, "docs_processed", 3.0),
            ("ocr", szekeres, "duration_s", 2.0),
            ("ocr", szekeres, "peak_rss_mb", 300.0),
            ("ocr", elliot, "duration_s", 5.0),
        ],
    )
    log = obs.RunLog(config.run_dir(run_id), run_id=run_id, dag="default")
    log.event("run_started", message="started")
    log.event("doc_finished", node="ocr", doc_id=szekeres, message="ocr done")
    log.event("doc_failed", level="ERROR", node="ocr", doc_id=scan, message="ocr failed")
    log.close()
    catalog.set_run_status(run_id, "succeeded")

    run2 = catalog.create_run("default", {"dag": "default", "nodes": ["embed"]}, scratch=False, commit="abc", dirty=False)
    embed2 = _version(
        catalog, objects, node="embed", run_id=run2, snapshot_id=snapshot_id, artifact_type="embed",
        records={szekeres: embed_table(szekeres, ["aerial mapping fee $1,750.00"], [[0, 1, 0, 0]])},
        inputs={"chunk": chunk.id}, meta=describe,
    )
    catalog.set_run_status(run2, "succeeded")

    run3 = catalog.create_run("default", {"dag": "default", "nodes": ["chunk"], "docs": [szekeres]}, scratch=True, commit="abc", dirty=True)
    scratch = _version(
        catalog, objects, node="chunk", run_id=run3, snapshot_id=snapshot_id, artifact_type="chunk", scratch=True,
        records={szekeres: chunk_table(szekeres, ["aerial mapping fee $1,750.00", "new terms"])},
        inputs={"ocr": ocr.id}, meta={"params": {"max_tokens": 64}},
    )
    obs.debug_sink(config.run_dir(run3), "chunk", szekeres)("page.json", {"a": 1})
    catalog.set_run_status(run3, "succeeded")

    active = catalog.create_run("default", {"dag": "default", "nodes": ["chunk", "embed"]}, scratch=False, commit="abc", dirty=False)
    catalog.set_run_status(active, "running")
    catalog.add_event(active, "doc_finished", {"node": "chunk", "doc_id": szekeres})
    catalog.add_event(active, "doc_failed", {"node": "chunk", "doc_id": elliot})

    yield World(config, catalog, objects, snapshot_id, docs, run_id, active, ocr, chunk, embed, embed2, scratch)
    catalog.close()


@pytest.fixture
def client(world: World) -> Iterator[TestClient]:
    with TestClient(create_app(world.config)) as c:
        yield c
