"""The `ingest` command line over a toy DAG in a temporary repo."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from ingest.cli import app
from ingest.config import load_config
from ingest.store.catalog import Catalog

NODES = Path(__file__).parent / "engine" / "nodes"
REPO = Path(__file__).resolve().parents[1]
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


def commit_all(repo: Path) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "c")


class Cli:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.runner = CliRunner()

    def __call__(self, *args: str, input: str | None = None, code: int = 0) -> str:
        result: Result = self.runner.invoke(app, ["--repo", str(self.repo), *args], input=input)
        assert result.exit_code == code, f"{args}: exit {result.exit_code}\n{result.output}\n{result.exception!r}"
        return result.output

    def catalog(self) -> Catalog:
        return Catalog(load_config(self.repo).catalog_path)

    def doc_id(self, path_part: str) -> str:
        catalog = self.catalog()
        try:
            snap = catalog.latest_snapshot("proposals")
            assert snap is not None
            return next(d.doc_id for d in catalog.snapshot_docs(snap.id) if path_part in d.paths[0])
        finally:
            catalog.close()


@pytest.fixture
def cli(tmp_repo: Path) -> Cli:
    shutil.copytree(NODES, tmp_repo / "nodes", ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_repo / "dags").mkdir()
    (tmp_repo / "dags" / "toy.yaml").write_text(TOY_DAG)
    commit_all(tmp_repo)
    return Cli(tmp_repo)


@pytest.fixture
def synced(cli: Cli) -> Cli:
    cli("sync")
    return cli


def wait_for(cond: Callable[[], bool], timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.2)


def test_sync_and_unchanged_resync(cli: Cli, tmp_path: Path) -> None:
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "broken.pdf").write_bytes(b"%PDF-1.4\n1 0 obj\n<<")
    with (cli.repo / "ingest.toml").open("a") as f:
        f.write(f'\n[sources.extra]\nroot = "{extra}"\n')

    out = cli("sync")
    assert "proposals  snapshot 1 (new) · 6 docs · 7 files" in out
    assert "added 6 · removed 0 · changed paths 0 · unchanged 0" in out
    assert re.search(r"extra  snapshot \d+ \(new\) · 1 docs", out)
    assert re.search(r"corrupt\s+broken\.pdf\s+\S", out)

    out = cli("sync", "proposals")
    assert "proposals  snapshot 1 (unchanged) · 6 docs" in out
    assert "added 0 · removed 0 · changed paths 0 · unchanged 6" in out
    assert "extra" not in out
    assert "unknown source" in cli("sync", "nope", code=1)


def test_validate(cli: Cli) -> None:
    assert "✓ toy  dags/toy.yaml · 3 nodes" in cli("validate")
    (cli.repo / "dags" / "bad.yaml").write_text(
        "name: bad\nsource: proposals\nnodes:\n  a:\n    script: nodes/size.py\n    inputs: [missing]\n"
    )
    out = cli("validate", code=1)
    assert "✗ dags/bad.yaml: node 'a': unknown input 'missing'" in out


def test_run_foreground(synced: Cli) -> None:
    out = synced("run", "toy")
    assert re.search(r"toy  snapshot 1 · 6 docs · committed [0-9a-f]{10}", out)
    assert re.search(r"size\s+6\s+0\s+0", out)
    assert "Promotable" in out
    assert "run 1 started" in out
    assert re.search(r"size  toy/size@v1 · ok 6 · error 0 · skipped 0 · cached 0", out)
    assert "run 1 succeeded" in out
    assert re.search(r"node\s+processed\s+cached\s+failed\s+skipped\s+wall\s+version", out)
    assert re.search(r"total\s+6\s+0\s+0\s+0\s+\S+\s+toy/total@v1", out)

    out = synced("run", "toy", "--node", "size")
    assert re.search(r"size\s+0\s+6\s+0", out)
    assert re.search(r"size\s+0\s+6\s+0\s+0\s+\S+\s+toy/size@v1 \(reused\)", out)

    assert "unknown node 'nope'" in synced("run", "toy", "--node", "nope", code=1)
    assert "has no current version" in synced("run", "toy", "--node", "rows", code=1)


def test_run_doc_expansion(synced: Cli) -> None:
    other = synced.doc_id("ELLIOT")
    out = synced("run", "toy", "--to", "rows", "--doc", "2292", "--doc", other[:8])
    assert "· 2 docs ·" in out
    assert "Scratch: doc subset" in out
    catalog = synced.catalog()
    try:
        v = catalog.find_version("toy", "rows", 1)
        assert v is not None and v.scratch
        assert {d.doc_id for d in catalog.version_docs(v.id)} == {synced.doc_id(SZEKERES), other}
    finally:
        catalog.close()
    assert "not found" in synced("run", "toy", "--doc", "9999", code=1)
    assert "not found" in synced("run", "toy", "--doc", "abc", code=1)
    assert "one of --node" in synced("run", "toy", "--node", "size", "--to", "rows", code=1)


def test_background_status_and_logs(synced: Cli) -> None:
    out = synced("status")
    assert re.search(r"size\s+never run\s+—\s+—\s+0", out)

    out = synced("run", "toy", "--background")
    run_id = int(re.search(r"^run (\d+)$", out, re.M).group(1))  # type: ignore[union-attr]
    catalog = synced.catalog()
    try:
        wait_for(lambda: catalog.get_run(run_id).status in ("succeeded", "failed", "cancelled"))
        assert catalog.get_run(run_id).status == "succeeded"
    finally:
        catalog.close()

    out = synced("status", "toy")
    assert re.search(r"rows\s+candidate\s+—\s+v1\s+0", out)
    assert re.search(rf"^  {run_id}\s+succeeded\s+size,rows,total\s+\S+s$", out, re.M)

    out = synced("logs", str(run_id))
    assert "run_started" not in out and f"run {run_id} started" in out
    assert "doc_finished" in out

    szekeres = synced.doc_id(SZEKERES)
    out = synced("logs", str(run_id), "--doc", "2292")
    lines = out.strip().splitlines()
    assert lines and all(szekeres[:10] in line for line in lines)
    assert {"size", "rows", "total"} <= {line.split()[2] for line in lines}

    out = synced("logs", str(run_id), "--node", "rows", "--level", "WARNING")
    assert all(" rows " in line for line in out.strip().splitlines())
    assert "not found" in synced("logs", "999", code=1)
    assert "is succeeded" in synced("cancel", str(run_id), code=1)


def test_status_after_param_change(synced: Cli) -> None:
    synced("run", "toy")
    for node in ("size", "rows", "total"):
        assert "is current" in synced("promote", f"toy/{node}@v1", "--yes")
    out = synced("status", "toy")
    for node in ("size", "rows", "total"):
        assert re.search(rf"{node}\s+fresh\s+v1\s+—\s+0\s*$", out, re.M)

    dag = synced.repo / "dags" / "toy.yaml"
    dag.write_text(TOY_DAG.replace("{n: 2}", "{n: 3}"))
    out = synced("status")
    assert re.search(r"size\s+fresh\s+v1\s+—\s+0\s*$", out, re.M)
    assert re.search(r"rows\s+stale\s+v1\s+—\s+0\s+uncommitted\s+code or params changed", out)
    assert re.search(r"total\s+stale\s+v1\s+—\s+0\s+input rows is stale", out)


def test_show_version_and_doc(synced: Cli) -> None:
    synced("run", "toy")
    out = synced("show", "toy/rows@v1")
    assert "toy/rows@v1  complete · promotable" in out
    assert re.search(r"inputs\s+size: toy/size@v1", out)
    assert re.search(r"snapshot\s+1", out)
    assert re.search(r"fingerprint\s+[0-9a-f]{16}", out)
    assert "summary  docs 6 · ok 6 · failed 0 · skipped 0 · size" in out
    assert "records 12" in out
    assert re.search(r"script\s+warning\s+6", out)
    assert "revalidate" not in out

    out = synced("show", "toy/rows@v1", "--doc", "2292")
    assert synced.doc_id(SZEKERES) in out
    assert SZEKERES in out
    assert "status  ok" in out
    assert re.search(r"script\s+warning\s+toy_warning", out)
    assert "2 rows × 3 columns: doc_id, i, size" in out

    out = synced("show", "toy/size@v1", "--doc", "2292")
    assert '"salt": 1' in out

    assert "no current version; using toy/rows@v1" in synced("show", "toy/rows")
    assert "not found" in synced("show", "toy/rows@v9", code=1)


def test_show_failed_doc(synced: Cli) -> None:
    dag = synced.repo / "dags" / "toy.yaml"
    dag.write_text(TOY_DAG.replace("{salt: 1}", "{salt: 1, raise_on: Szekeres}"))
    commit_all(synced.repo)
    out = synced("run", "toy", "--node", "size")
    assert re.search(r"✗ size [0-9a-f]{10}  ValueError: cannot size 2292", out)
    assert re.search(r"size\s+6\s+0\s+1\s+0\s+\S+\s+toy/size@v1", out)
    out = synced("show", "toy/size@v1")
    assert re.search(r"failures\n  [0-9a-f]{10}\s+2292 SZEKERES.*ValueError: cannot size", out)
    out = synced("show", "toy/size@v1", "--doc", "2292")
    assert "status  error" in out
    assert "traceback" in out and "raise ValueError" in out


def test_diff_and_promote(synced: Cli) -> None:
    synced("run", "toy")
    dag = synced.repo / "dags" / "toy.yaml"
    dag.write_text(TOY_DAG.replace("{salt: 1}", "{salt: 2}"))
    commit_all(synced.repo)
    synced("run", "toy", "--node", "size")

    out = synced("diff", "toy/size@v1", "toy/size@v2")
    assert re.search(r"toy/size@v1\s+toy/size@v2", out)
    assert re.search(r"docs\s+6\s+6", out)
    assert "changed 6 · unchanged 0" in out
    out = synced("diff", "toy/size@v1", "toy/size@v2", "--limit", "2")
    assert len(re.findall(r"^  [0-9a-f]{10}\s", out, re.M)) == 2

    out = synced("diff", "toy/size@v1", "toy/size@v2", "--doc", "2292")
    assert '-  "salt": 1' in out and '+  "salt": 2' in out
    out = synced("diff", "toy/rows@v1", "toy/rows@v1", "--doc", "2292")
    assert "only a 0 · only b 0" in out

    out = synced("promote", "toy/rows@v1", input="y\n")
    assert "promote toy/rows@v1, replacing nothing" in out
    assert re.search(r"script\s+warning\s+6", out)
    assert "! built from toy/size@v1, but the current toy/size is none" in out
    assert "toy/rows@v1 is current" in out
    published = synced.repo / ".ingest" / "published" / "toy" / "rows.parquet"
    assert f"published {published}" in out and published.exists()

    out = synced("promote", "toy/size@v2", input="n\n", code=1)
    assert "is current" not in out
    out = synced("promote", "toy/size@v2", "--yes")
    assert "toy/size@v2 is current" in out and "published" not in out
    assert "already current" in synced("promote", "toy/size@v2", "--yes", code=1)

    synced("run", "toy", "--node", "size", "--doc", "2292")
    out = synced("promote", "toy/size@v3", "--yes", code=1)
    assert "not promotable: scratch version" in out


def test_revalidate_after_checks_edit(synced: Cli) -> None:
    synced("run", "toy")
    checks = synced.repo / "nodes" / "checks" / "rows.py"
    checks.write_text(
        checks.read_text().replace(
            "    return issues", '    issues.append(Issue("warning", "second", "another one"))\n    return issues'
        )
    )
    out = synced("show", "toy/rows@v1")
    assert re.search(r"script\s+warning\s+6", out)
    assert "checks file changed: revalidate toy/rows@v1" in out
    assert "toy/rows@v1: 12 issue(s)" in synced("revalidate", "toy/rows@v1")
    out = synced("show", "toy/rows@v1")
    assert re.search(r"script\s+warning\s+12", out)
    assert "revalidate" not in out


def test_doctor_missing_source(cli: Cli, tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    (cli.repo / "ingest.toml").write_text(f'data_dir = ".ingest"\n\n[sources.proposals]\nroot = "{missing}"\n')
    out = cli("doctor", code=1)
    assert f"✗ source proposals: {missing} not readable" in out
    assert "✓ DAGs valid (toy)" in out
    assert re.search(r"^[✓✗] Python 3\.\d+", out, re.M)
    assert re.search(r"^[✓!] free disk", out, re.M)


def test_ui_not_available(cli: Cli) -> None:
    assert "The UI isn't available yet." in cli("ui", code=1)


@pytest.mark.models
def test_models_pull_and_default_run(tmp_repo: Path) -> None:
    shutil.copytree(REPO / "dags", tmp_repo / "dags")
    shutil.copytree(REPO / "nodes", tmp_repo / "nodes", ignore=shutil.ignore_patterns("__pycache__"))
    commit_all(tmp_repo)
    cli = Cli(tmp_repo)
    out = cli("models", "pull")
    for node in ("ocr", "chunk", "embed"):
        assert re.search(rf"^✓ default/{node}  ", out, re.M), out
    cli("sync")
    out = cli("run", "default", "--sample", "2")
    assert "Scratch: doc subset" in out
    for node in ("ocr", "chunk", "embed"):
        assert re.search(rf"^{node}  default/{node}@v1 · ok 2 · error 0", out, re.M), out
    assert "run 1 succeeded" in out


def test_bare_refs(synced: Cli) -> None:
    synced("run", "toy")
    out = synced("show", "toy/rows")
    assert "toy/rows@v1" in out
    assert "no current version; using toy/rows@v1" in out
    assert "toy/size@v1 is current" in synced("promote", "toy/size", "--yes")
    assert "already current" in synced("promote", "toy/size", "--yes", code=1)
    assert "has no promotable version" in synced("promote", "toy/nope", "--yes", code=1)
