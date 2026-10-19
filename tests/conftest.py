"""Shared test fixtures. Component-specific fixtures live next to their tests."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "proposals"
REAL_CORPUS = Path("/Users/Shared/proposals")


@pytest.fixture
def fixtures_dir() -> Path:
    """Seven real proposal PDFs (six docs: the two Yardley files are byte-identical)."""
    return FIXTURES


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """An empty git repo with ingest.toml whose `proposals` source points at the fixtures and
    whose data dir is <repo>/.ingest. Tests add dags/ and nodes/ as needed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "ingest.toml").write_text(
        f'data_dir = ".ingest"\n\n[sources.proposals]\nroot = "{FIXTURES}"\nglob = "**/*.pdf"\n'
    )
    (repo / ".gitignore").write_text(".ingest/\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    return repo


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("INGEST_REAL_CORPUS") == "1" and REAL_CORPUS.exists():
        return
    skip = pytest.mark.skip(reason="set INGEST_REAL_CORPUS=1 to run against the real corpus")
    for item in items:
        if "corpus" in item.keywords:
            item.add_marker(skip)
