"""Fixtures for the query app's tests. Component-specific helpers live in their test modules."""

from __future__ import annotations

from pathlib import Path

import pytest

from ingest.config import Config, load_config
from ingest.query import QueryConfig


@pytest.fixture
def config(tmp_repo: Path) -> Config:
    """Config of tmp_repo: data dir <repo>/.ingest, `proposals` source at the fixtures."""
    return load_config(tmp_repo)


@pytest.fixture
def qconfig() -> QueryConfig:
    return QueryConfig()
