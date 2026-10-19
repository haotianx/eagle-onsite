from __future__ import annotations

from pathlib import Path

import pytest

from ingest.config import Config
from ingest.query import QueryConfig, load_query_config


def _config(repo: Path, toml: str) -> Config:
    (repo / "ingest.toml").write_text(toml)
    return Config(repo_root=repo, data_dir=repo / ".ingest", sources={})


def test_defaults_without_query_table(tmp_path: Path) -> None:
    assert load_query_config(_config(tmp_path, 'data_dir = ".ingest"\n')) == QueryConfig()


def test_reads_query_table(tmp_path: Path) -> None:
    q = load_query_config(_config(tmp_path, '[query]\ntop_k = 5\nmodel = "claude-opus-5-5"\n'))
    assert (q.top_k, q.model, q.max_rounds, q.ref) == (5, "claude-opus-5-5", 3, "default/embed")


@pytest.mark.parametrize("body", ["bogus = 1", 'top_k = "8"', "model = 3", "max_rounds = true"])
def test_rejects_bad_keys(tmp_path: Path, body: str) -> None:
    with pytest.raises(ValueError):
        load_query_config(_config(tmp_path, f"[query]\n{body}\n"))
