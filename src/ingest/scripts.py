"""Loading node scripts and checks files."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

from ingest.contract import ARTIFACT_TYPES, ArtifactType, Issue


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


def _parse(path: Path) -> ast.Module | str:
    try:
        return ast.parse(path.read_bytes(), filename=str(path))
    except OSError as e:
        return f"{path}: cannot read: {e.strerror or e}"
    except (SyntaxError, ValueError) as e:
        return f"{path}: syntax error: {e}"


def _module_names(tree: ast.Module) -> tuple[set[str], dict[str, ast.expr]]:
    """Module-level function names, and values assigned to module-level names."""
    funcs: set[str] = set()
    assigns: dict[str, ast.expr] = {}
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.add(stmt.name)
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    assigns[target.id] = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            if stmt.value is not None:
                assigns[stmt.target.id] = stmt.value
    return funcs, assigns


def inspect_script(path: Path) -> list[str]:
    """Static check without executing the file (AST): it must define exactly one of run /
    run_corpus at module level; ARTIFACT_TYPE, if assigned a literal, must be a known type.
    Returns error strings."""
    tree = _parse(path)
    if isinstance(tree, str):
        return [tree]
    funcs, assigns = _module_names(tree)
    errors = []
    has_run, has_corpus = "run" in funcs, "run_corpus" in funcs
    if has_run and has_corpus:
        errors.append(f"{path}: defines both run and run_corpus")
    elif not has_run and not has_corpus:
        errors.append(f"{path}: defines neither run nor run_corpus")
    value = assigns.get("ARTIFACT_TYPE")
    if isinstance(value, ast.Constant) and value.value not in ARTIFACT_TYPES:
        errors.append(
            f"{path}: ARTIFACT_TYPE {value.value!r} is not one of {', '.join(ARTIFACT_TYPES)}"
        )
    return errors


def inspect_checks(path: Path) -> list[str]:
    """Static check: a checks file must define validate() at module level."""
    tree = _parse(path)
    if isinstance(tree, str):
        return [tree]
    funcs, _ = _module_names(tree)
    return [] if "validate" in funcs else [f"{path}: checks file does not define validate()"]


def _import(path: Path, prefix: str) -> tuple[ModuleType, str]:
    path = path.resolve()
    try:
        data = path.read_bytes()
    except OSError as e:
        raise ScriptError(f"{path}: cannot read: {e.strerror or e}") from e
    source_hash = hashlib.sha256(data).hexdigest()
    path_hash = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
    name = f"_ingest_{prefix}_{path_hash}_{source_hash[:16]}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ScriptError(f"{path}: cannot import")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        del sys.modules[name]
        raise ScriptError(f"{path}: import failed: {type(e).__name__}: {e}") from e
    return module, source_hash


def _callable(module: ModuleType, path: Path, name: str) -> Callable[..., Any] | None:
    fn = getattr(module, name, None)
    if fn is not None and not callable(fn):
        raise ScriptError(f"{path}: {name} is not callable")
    return fn


def load_script(path: Path) -> LoadedScript:
    """Import the script from its file under a unique module name (two scripts with the same
    file name must not collide) and detect its functions. Raises ScriptError."""
    module, source_hash = _import(path, "script")
    run = _callable(module, path, "run")
    run_corpus = _callable(module, path, "run_corpus")
    if run is not None and run_corpus is not None:
        raise ScriptError(f"{path}: defines both run and run_corpus")
    if run is None and run_corpus is None:
        raise ScriptError(f"{path}: defines neither run nor run_corpus")
    artifact_type = getattr(module, "ARTIFACT_TYPE", None)
    if artifact_type is not None and artifact_type not in ARTIFACT_TYPES:
        raise ScriptError(
            f"{path}: ARTIFACT_TYPE {artifact_type!r} is not one of {', '.join(ARTIFACT_TYPES)}"
        )
    return LoadedScript(
        path=path.resolve(),
        source_hash=source_hash,
        kind="per_doc" if run is not None else "corpus",
        artifact_type=artifact_type,
        run=run,
        run_corpus=run_corpus,
        setup=_callable(module, path, "setup"),
        describe=_callable(module, path, "describe"),
    )


def load_checks(path: Path) -> LoadedChecks:
    module, source_hash = _import(path, "checks")
    validate = _callable(module, path, "validate")
    if validate is None:
        raise ScriptError(f"{path}: checks file does not define validate()")
    return LoadedChecks(path=path.resolve(), source_hash=source_hash, validate=validate)
