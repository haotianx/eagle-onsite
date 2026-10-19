"""Repo configuration: ingest.toml plus environment overrides."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

CONFIG_FILE = "ingest.toml"
DATA_DIR_ENV = "INGEST_DATA_DIR"


@dataclass(frozen=True)
class SourceConfig:
    name: str
    root: Path
    """Absolute path. Relative paths in ingest.toml resolve against the repo root."""
    glob: str = "**/*.pdf"
    """Matched case-insensitively on the file suffix (".PDF" counts)."""


@dataclass(frozen=True)
class Config:
    repo_root: Path
    data_dir: Path
    """Absolute. Default <repo>/.ingest; ingest.toml `data_dir` or $INGEST_DATA_DIR override."""
    sources: Mapping[str, SourceConfig]

    @property
    def catalog_path(self) -> Path:
        return self.data_dir / "catalog.sqlite"

    @property
    def objects_dir(self) -> Path:
        return self.data_dir / "objects"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def published_dir(self) -> Path:
        return self.data_dir / "published"

    def run_dir(self, run_id: int) -> Path:
        return self.runs_dir / str(run_id)


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from `start` (default cwd) to the first directory containing ingest.toml."""
    raise NotImplementedError


def load_config(repo_root: Path | None = None) -> Config:
    """Read <repo_root>/ingest.toml (repo_root found via find_repo_root when None).

    Format:
        data_dir = ".ingest"            # optional
        [sources.<name>]
        root = "/abs/or/relative/path"
        glob = "**/*.pdf"               # optional
    """
    raise NotImplementedError
