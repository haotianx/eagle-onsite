"""Helpers for the engine tests: a toy repo with the node scripts in tests/engine/nodes."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from ingest.config import Config, load_config
from ingest.engine.executor import RunResult, run
from ingest.engine.planner import RunRequest
from ingest.sources import sync
from ingest.store.catalog import Catalog

NODES = Path(__file__).parent / "nodes"
SZEKERES = "2292 SZEKERES/CONTRACT/AEROTECH/2292 Szekeres Proposal.pdf"

TOY_DAG = """\
name: toy
source: proposals
nodes:
  size:
    script: nodes/size.py
    params: {salt: 1}
    workers: 2
    timeout: 30
  rows:
    script: nodes/rows.py
    inputs: [size]
    params: {n: 2}
    checks: nodes/checks/rows.py
    workers: 2
    timeout: 30
  total:
    script: nodes/total.py
    inputs: [rows]
    workers: 1
    timeout: 30
"""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def commit_all(repo: Path) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "c")
    return git(repo, "rev-parse", "HEAD").strip()


def write_dag(repo: Path, text: str, name: str = "toy") -> None:
    path = repo / "dags" / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class Env:
    """A toy repo, synced, with an open catalog."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.config: Config = load_config(repo)
        self.catalog = Catalog(self.config.catalog_path)
        self.snapshot_id = sync(self.catalog, self.config.sources["proposals"]).snapshot_id
        self.doc_ids = [d.doc_id for d in self.catalog.snapshot_docs(self.snapshot_id)]

    def run(self, dag: str = "toy", **kw: Any) -> RunResult:
        return run(self.config, RunRequest(dag=dag, **kw))

    def doc_id(self, path_part: str) -> str:
        return next(d.doc_id for d in self.catalog.snapshot_docs(self.snapshot_id) if path_part in d.paths[0])

    def docs(self, version_id: int) -> dict[str, Any]:
        return {d.doc_id: d for d in self.catalog.version_docs(version_id)}

    def metrics(self, run_id: int, node: str | None = None, doc_id: str | None = None) -> dict[str, float]:
        return {
            m.name: m.value
            for m in self.catalog.run_metrics(run_id)
            if m.node == node and m.doc_id == doc_id
        }


@pytest.fixture
def env(tmp_repo: Path) -> Iterator[Env]:
    shutil.copytree(NODES, tmp_repo / "nodes")
    write_dag(tmp_repo, TOY_DAG)
    commit_all(tmp_repo)
    e = Env(tmp_repo)
    yield e
    e.catalog.close()
