"""Hashes and fingerprints. All digests are lowercase SHA-256 hex."""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_CHUNK = 1 << 20


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Streamed; works for large files."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(_CHUNK):
            h.update(block)
    return h.hexdigest()


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, compact separators, UTF-8 (ensure_ascii=False)."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _hash_obj(obj: Any) -> str:
    return sha256_bytes(canonical_json(obj).encode("utf-8"))


def params_hash(params: Mapping[str, Any]) -> str:
    return _hash_obj(dict(params))


def env_hash(repo_root: Path) -> str:
    """Environment identity: uv.lock bytes, Python major.minor, and ingest.__version__.
    A missing uv.lock hashes as empty."""
    from ingest import __version__

    lock = repo_root / "uv.lock"
    lock_hash = sha256_file(lock) if lock.is_file() else sha256_bytes(b"")
    python = f"{sys.version_info.major}.{sys.version_info.minor}"
    return _hash_obj({"uv_lock": lock_hash, "python": python, "ingest": __version__})


def node_fingerprint(
    script_hash: str,
    params: Mapping[str, Any],
    env: str,
    input_fingerprints: Mapping[str, str],
) -> str:
    """hash(script hash, params, env, sorted (input name, input fingerprint) pairs). Execution
    settings (timeout, workers) and checks are deliberately excluded."""
    return _hash_obj(
        {
            "script": script_hash,
            "params": dict(params),
            "env": env,
            "inputs": sorted(input_fingerprints.items()),
        }
    )


def checks_fingerprint(checks_hash: str, params: Mapping[str, Any]) -> str:
    return _hash_obj({"checks": checks_hash, "params": dict(params)})


def dag_fingerprints(
    dag: Any,
    root: Path,
    env: str,
    overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Fingerprint every node of a DagSpec in topo order, reading scripts under `root`.
    `overrides` maps a node name to the fingerprint to use for it instead (when a run reads a
    chosen older version of that node); downstream fingerprints chain from the override."""
    overrides = overrides or {}
    fps: dict[str, str] = {}
    script_hashes: dict[str, str] = {}
    for name in dag.topo_order():
        if name in overrides:
            fps[name] = overrides[name]
            continue
        node = dag.nodes[name]
        if node.script not in script_hashes:
            script_hashes[node.script] = sha256_file(root / node.script)
        fps[name] = node_fingerprint(
            script_hashes[node.script],
            node.params,
            env,
            {i: fps[i] for i in node.inputs},
        )
    return fps
