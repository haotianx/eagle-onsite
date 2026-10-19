"""Repo configuration: ingest.toml plus environment overrides."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

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

    @property
    def uploads_dir(self) -> Path:
        """Where the query app stores uploaded PDFs; a folder of the default source."""
        return self.data_dir / "uploads"

    def run_dir(self, run_id: int) -> Path:
        return self.runs_dir / str(run_id)


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from `start` (default cwd) to the first directory containing ingest.toml."""
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / CONFIG_FILE).is_file():
            return d
    raise FileNotFoundError(f"no {CONFIG_FILE} found in {here} or its parents")


def load_config(repo_root: Path | None = None) -> Config:
    """Read <repo_root>/ingest.toml (repo_root found via find_repo_root when None).

    Format:
        data_dir = ".ingest"            # optional
        [sources.<name>]
        root = "/abs/or/relative/path"
        glob = "**/*.pdf"               # optional
    """
    root = find_repo_root() if repo_root is None else repo_root.resolve()
    path = root / CONFIG_FILE
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path}: {e}") from e
    unknown = set(raw) - {"data_dir", "sources"}
    if unknown:
        raise ValueError(f"{path}: unknown keys {sorted(unknown)}")

    data_dir = os.environ.get(DATA_DIR_ENV) or raw.get("data_dir", ".ingest")
    if not isinstance(data_dir, str):
        raise ValueError(f"{path}: data_dir must be a string")

    sources_raw = raw.get("sources", {})
    if not isinstance(sources_raw, dict):
        raise ValueError(f"{path}: [sources] must be a table")
    sources = {name: _source(path, root, name, s) for name, s in sources_raw.items()}
    return Config(
        repo_root=root,
        data_dir=_resolve(root, data_dir),
        sources=MappingProxyType(sources),
    )


def _source(path: Path, root: Path, name: str, raw: Any) -> SourceConfig:
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: [sources.{name}] must be a table")
    unknown = set(raw) - {"root", "glob"}
    if unknown:
        raise ValueError(f"{path}: [sources.{name}] unknown keys {sorted(unknown)}")
    src_root, glob = raw.get("root"), raw.get("glob", "**/*.pdf")
    if not isinstance(src_root, str) or not src_root:
        raise ValueError(f"{path}: [sources.{name}] root must be a non-empty string")
    if not isinstance(glob, str) or not glob:
        raise ValueError(f"{path}: [sources.{name}] glob must be a non-empty string")
    return SourceConfig(name=name, root=_resolve(root, src_root), glob=glob)


def _resolve(root: Path, value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else (root / p).resolve()
