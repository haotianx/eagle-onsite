"""Git state of the repo: HEAD, uncommitted paths, exporting HEAD files, watching."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from pathlib import Path


def head_commit(repo_root: Path) -> str | None:
    """Full SHA of HEAD, or None if the repo has no commits or isn't a git repo."""
    raise NotImplementedError


def dirty_paths(repo_root: Path) -> set[str]:
    """Repo-relative POSIX paths that differ from HEAD: modified, staged, deleted, or untracked
    (ignored files excluded)."""
    raise NotImplementedError


def is_dirty(repo_root: Path, paths: Iterable[str]) -> bool:
    """True if any of `paths` (repo-relative files) is in dirty_paths()."""
    raise NotImplementedError


def materialize_head(repo_root: Path, paths: Iterable[str], dest: Path) -> list[str]:
    """Write the HEAD version of each path (file or directory) under `dest`, keeping relative
    paths. Returns paths that don't exist in HEAD (skipped)."""
    raise NotImplementedError


def watch(
    repo_root: Path,
    paths: Iterable[str],
    callback: Callable[[set[str]], None],
    stop: threading.Event,
) -> None:
    """Block until `stop` is set, calling `callback(changed repo-relative paths)` whenever files
    under `paths` change (watchfiles)."""
    raise NotImplementedError
