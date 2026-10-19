"""The real default DAG (Docling → HybridChunker → Qwen3-Embedding) through the engine."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest
from engine_util import SZEKERES, Env, commit_all

from ingest.engine.executor import run
from ingest.engine.planner import RunRequest
from ingest.publish import version_table
from ingest.store.objects import ObjectStore

pytestmark = pytest.mark.models

REPO = Path(__file__).resolve().parents[2]
YARDLEY = "3257 YARDLEY- ARCHIVE FOLDER/CONTRACT/STAKING PROPOSAL.pdf"


def test_default_dag_end_to_end(tmp_repo: Path) -> None:
    shutil.copytree(REPO / "dags", tmp_repo / "dags")
    shutil.copytree(REPO / "nodes", tmp_repo / "nodes", ignore=shutil.ignore_patterns("__pycache__"))
    commit_all(tmp_repo)
    e = Env(tmp_repo)
    try:
        docs = [e.doc_id(SZEKERES), e.doc_id(YARDLEY)]
        result = run(e.config, RunRequest(dag="default", docs=docs))
        assert result.status == "succeeded", e.catalog.get_run(result.run_id).error
        assert set(result.versions) == {"ocr", "chunk", "embed"}
        for node, vid in result.versions.items():
            v = e.catalog.get_version(vid)
            assert v.stats["ok"] == 2, [(d.error_type, d.error_message) for d in e.catalog.version_docs(vid)]
            assert v.meta["code"] == "committed" and v.scratch
        ocr = e.catalog.get_version(result.versions["ocr"])
        assert ocr.stats["totals"]["pages"] >= 3
        assert e.metrics(result.run_id, "ocr")["pages_per_s"] > 0

        embed = e.catalog.get_version(result.versions["embed"])
        assert embed.meta["describe"]["dim"] == 1024
        table = version_table(e.catalog, ObjectStore(e.config.objects_dir), embed.id)
        assert table.num_rows == embed.stats["totals"]["vectors"] > 0
        assert table.schema.field("vector").type == pa.list_(pa.float32(), 1024)
        vectors = np.array(table.column("vector").to_pylist(), dtype=np.float32)
        assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-3)
        szekeres = table.filter(pa.compute.equal(table.column("doc_id"), docs[0]))
        assert any("1,750.00" in t for t in szekeres.column("text").to_pylist())
        assert set(table.column("job_number").to_pylist()) == {"2292", "3257"}
    finally:
        e.catalog.close()
