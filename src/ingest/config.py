"""Repo configuration: ingest.toml plus environment overrides."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

CONFIG_FILE = "ingest.toml"
DATA_DIR_ENV = "INGEST_DATA_DIR"


@dataclass(frozen=True, init=False)
class SourceConfig:
    name: str
    folders: tuple[Path, ...]
    """Absolute paths; the first is the primary folder. Relative paths in ingest.toml resolve
    against the repo root, and `{data_dir}` expands to the data dir."""
    glob: str = "**/*.pdf"
    """Matched case-insensitively on the file suffix (".PDF" counts)."""

    def __init__(
        self,
        name: str,
        folders: Sequence[Path] = (),
        glob: str = "**/*.pdf",
        *,
        root: Path | None = None,
    ) -> None:
        """Pass `folders`, or `root` for a single-folder source."""
        if root is not None:
            if folders:
                raise ValueError(f"source {name!r}: set root or folders, not both")
            folders = (root,)
        if not folders:
            raise ValueError(f"source {name!r}: at least one folder is required")
        names = [Path(f).name for f in folders[1:]]
        if any(not n for n in names) or len(set(names)) != len(names):
            raise ValueError(f"source {name!r}: folder names after the first must be unique")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "folders", tuple(Path(f) for f in folders))
        object.__setattr__(self, "glob", glob)

    @property
    def root(self) -> Path:
        """The primary folder."""
        return self.folders[0]


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
        folders = ["/abs/or/relative/path", "{data_dir}/uploads"]   # or root = "<one folder>"
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
    data_path = _resolve(root, data_dir)
    sources = {name: _source(path, root, data_path, name, s) for name, s in sources_raw.items()}
    return Config(repo_root=root, data_dir=data_path, sources=MappingProxyType(sources))


def _source(path: Path, root: Path, data_dir: Path, name: str, raw: Any) -> SourceConfig:
    where = f"{path}: [sources.{name}]"
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be a table")
    unknown = set(raw) - {"root", "folders", "glob"}
    if unknown:
        raise ValueError(f"{where} unknown keys {sorted(unknown)}")
    if ("root" in raw) == ("folders" in raw):
        raise ValueError(f"{where} needs exactly one of root or folders")
    folders = [raw["root"]] if "root" in raw else raw["folders"]
    if not isinstance(folders, list) or not folders:
        raise ValueError(f"{where} folders must be a non-empty list")
    if not all(isinstance(f, str) and f for f in folders):
        raise ValueError(f"{where} root and folders must be non-empty strings")
    glob = raw.get("glob", "**/*.pdf")
    if not isinstance(glob, str) or not glob:
        raise ValueError(f"{where} glob must be a non-empty string")
    resolved = [_resolve(root, f.replace("{data_dir}", str(data_dir))) for f in folders]
    try:
        return SourceConfig(name=name, folders=resolved, glob=glob)
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from e


def _resolve(root: Path, value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else (root / p).resolve()
