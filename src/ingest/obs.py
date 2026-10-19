"""Logging, per-doc output capture, debug files, process metrics.

On-disk layout per run (config.run_dir(run_id)):
    log.jsonl                         every line from the run (engine events + captured doc output)
    debug/<node>/<doc_id>/<name>      ctx.debug() files (scratch runs only)

A log line is a JSON object with at least: ts (UTC ISO-8601), level ("DEBUG".."CRITICAL"),
kind ("event" | "log" | "warning" | "stdout" | "stderr"), message, run_id, dag, and when known
node, version_id, doc_id. Event lines also carry `event` (e.g. "run_started", "node_planned",
"doc_cached", "doc_started", "doc_finished", "doc_failed", "node_finished", "run_finished")
plus event-specific fields.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ingest.contract import DebugSink

LOG_FILE = "log.jsonl"
DEBUG_DIR = "debug"
MAX_CAPTURE_BYTES = 1_000_000


def configure_environment(*, offline: bool) -> None:
    """Disable third-party telemetry (HF_HUB_DISABLE_TELEMETRY=1, DO_NOT_TRACK=1), set
    TOKENIZERS_PARALLELISM=false, and HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1 when `offline`.
    Call before importing ML libraries in any process that runs nodes."""
    raise NotImplementedError


def configure_logging(level: str = "INFO") -> None:
    """Human-readable console logging for the CLI."""
    raise NotImplementedError


class RunLog:
    """Append-only JSON-lines writer for one run. Used only by the executor parent process."""

    def __init__(self, run_dir: Path, *, run_id: int, dag: str) -> None:
        raise NotImplementedError

    def event(
        self,
        event: str,
        *,
        level: str = "INFO",
        message: str = "",
        node: str | None = None,
        version_id: int | None = None,
        doc_id: str | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        """Write and return an event line."""
        raise NotImplementedError

    def write_lines(self, lines: Iterable[dict[str, Any]]) -> None:
        """Append captured lines (from a worker) as-is."""
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


def read_logs(
    run_dir: Path,
    *,
    node: str | None = None,
    doc_id: str | None = None,
    level: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Lines in file order, filtered; `level` is a minimum level."""
    raise NotImplementedError


@dataclass
class Captured:
    lines: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False


@contextmanager
def capture(
    *,
    run_id: int,
    dag: str,
    node: str,
    version_id: int | None,
    doc_id: str | None,
    max_bytes: int = MAX_CAPTURE_BYTES,
) -> Iterator[Captured]:
    """Capture everything a node emits while the block runs: Python logging (all loggers),
    warnings.warn, sys.stdout/sys.stderr, and native writes to file descriptors 1 and 2 (e.g. from
    C extensions), as tagged log lines. Used inside worker processes around each run() call.
    Output beyond `max_bytes` is dropped and `truncated` set (a final line says so)."""
    raise NotImplementedError
    yield


def debug_sink(run_dir: Path, node: str, doc_id: str) -> DebugSink:
    """A sink for NodeContext: writes run_dir/debug/<node>/<doc_id>/<name>. bytes are written as
    is, str as UTF-8, dict/list as JSON. Names are sanitized to a single path component."""
    raise NotImplementedError


def list_debug_files(run_dir: Path, node: str, doc_id: str) -> list[Path]:
    raise NotImplementedError


def rss_mb() -> float:
    """Current process resident set size in MB."""
    raise NotImplementedError


class PeakRss:
    """Context manager that samples this process's RSS on a background thread
    (every `interval` s) and exposes the maximum as `.peak_mb` after exit."""

    def __init__(self, interval: float = 0.05) -> None:
        self.interval = interval
        self.peak_mb = 0.0
        self._stop = threading.Event()

    def __enter__(self) -> PeakRss:
        raise NotImplementedError

    def __exit__(self, *exc: object) -> None:
        raise NotImplementedError
