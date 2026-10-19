"""Single-doc runs (Ingestion-Design §8): process one document for the query app's uploads
through the node chain recorded in the current embed version's lineage — its scripts at the
lineage's git commit, params and model revisions — so outputs land in the per-doc cache under the
same fingerprints. No version is created and no per-node lock is taken. One warm child process
keeps the chain's models loaded and processes queued docs one at a time, first in first out.

Each submitted doc gets a run (catalog kind "single_doc") whose catalog events are:
    queued, doc_started, node_finished {node, metrics (e.g. pages, chunks, vectors)},
    doc_finished {doc_id}, doc_failed {node, error_type, message}
and the run ends succeeded or failed. On success the doc is recorded with
Catalog.add_pending_doc against the embed version, so publish.library includes it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ingest.config import Config
from ingest.store.catalog import Catalog


@dataclass(frozen=True)
class LineageNode:
    node: str
    version_id: int
    node_fp: str
    script: str
    script_hash: str
    params: Mapping[str, Any]
    commit: str | None
    checks: str | None
    timeout: float


def lineage(catalog: Catalog, dag: str = "default", node: str = "embed") -> list[LineageNode]:
    """The node chain (in topo order, ending with `node`) recorded in the current version of
    dag/node via input_versions. Raises LookupError when there is no current version."""
    raise NotImplementedError


def docs_to_reprocess(catalog: Catalog, config: Config, dag: str = "default", node: str = "embed") -> list[str]:
    """Upload docs that aren't in the current version and aren't pending against it (e.g. after a
    promotion), excluding removed docs. The query app resubmits these."""
    raise NotImplementedError


class SingleDocRunner:
    """The warm single-doc worker. Thread-safe; owned by the API server."""

    def __init__(self, config: Config, *, dag: str = "default", node: str = "embed", online: bool = False) -> None:
        raise NotImplementedError

    def start(self) -> None:
        """Resolve the lineage and start the child process (loads all models once)."""
        raise NotImplementedError

    def submit(self, path: Path) -> int:
        """Queue a PDF that lives in a source folder (e.g. config.uploads_dir / "x.pdf"); returns
        the run id immediately. Progress is on catalog.events(run_id)."""
        raise NotImplementedError

    def reload(self) -> None:
        """Re-resolve the lineage after a promotion and restart the child with the new chain."""
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError
