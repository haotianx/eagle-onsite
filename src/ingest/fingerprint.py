"""Hashes and fingerprints. All digests are lowercase SHA-256 hex."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    raise NotImplementedError


def sha256_file(path: Path) -> str:
    """Streamed; works for large files."""
    raise NotImplementedError


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, compact separators, UTF-8 (ensure_ascii=False)."""
    raise NotImplementedError


def params_hash(params: Mapping[str, Any]) -> str:
    raise NotImplementedError


def env_hash(repo_root: Path) -> str:
    """Environment identity: uv.lock bytes, Python major.minor, and ingest.__version__.
    A missing uv.lock hashes as empty."""
    raise NotImplementedError


def node_fingerprint(
    script_hash: str,
    params: Mapping[str, Any],
    env: str,
    input_fingerprints: Mapping[str, str],
) -> str:
    """hash(script hash, params, env, sorted (input name, input fingerprint) pairs). Execution
    settings (timeout, workers) and checks are deliberately excluded."""
    raise NotImplementedError


def checks_fingerprint(checks_hash: str, params: Mapping[str, Any]) -> str:
    raise NotImplementedError


def dag_fingerprints(
    dag: Any,
    root: Path,
    env: str,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Fingerprint every node of a DagSpec in topo order, reading scripts under `root`.
    `overrides` maps a node name to the fingerprint to use for it instead (when a run reads a
    chosen older version of that node); downstream fingerprints chain from the override."""
    raise NotImplementedError
