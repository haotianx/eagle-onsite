"""Loading node scripts and checks files."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ingest.contract import ArtifactType, Issue


class ScriptError(Exception):
    pass


@dataclass(frozen=True)
class LoadedScript:
    path: Path
    source_hash: str
    """SHA-256 of the file bytes."""
    kind: Literal["per_doc", "corpus"]
    artifact_type: ArtifactType | None
    run: Callable[..., Any] | None
    run_corpus: Callable[..., Any] | None
    setup: Callable[[Mapping[str, Any]], Any] | None
    describe: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None


@dataclass(frozen=True)
class LoadedChecks:
    path: Path
    source_hash: str
    validate: Callable[[Any, Mapping[str, Any]], list[Issue]]


def inspect_script(path: Path) -> list[str]:
    """Static check without executing the file (AST): it must define exactly one of run /
    run_corpus at module level; ARTIFACT_TYPE, if assigned a literal, must be a known type.
    Returns error strings."""
    raise NotImplementedError


def inspect_checks(path: Path) -> list[str]:
    """Static check: a checks file must define validate() at module level."""
    raise NotImplementedError


def load_script(path: Path) -> LoadedScript:
    """Import the script from its file under a unique module name (two scripts with the same
    file name must not collide) and detect its functions. Raises ScriptError."""
    raise NotImplementedError


def load_checks(path: Path) -> LoadedChecks:
    raise NotImplementedError
