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

import codecs
import ctypes
import io
import json
import logging
import os
import re
import sys
import threading
import warnings
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

from ingest.contract import DebugSink

LOG_FILE = "log.jsonl"
DEBUG_DIR = "debug"
MAX_CAPTURE_BYTES = 1_000_000

LEVELS: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _level_no(level: str) -> int:
    try:
        return LEVELS[level.upper()]
    except KeyError:
        raise ValueError(f"unknown log level {level!r}") from None


def _level_name(levelno: int) -> str:
    name = "DEBUG"
    for n, no in LEVELS.items():
        if levelno >= no:
            name = n
    return name


def configure_environment(*, offline: bool) -> None:
    """Disable third-party telemetry (HF_HUB_DISABLE_TELEMETRY=1, DO_NOT_TRACK=1), set
    TOKENIZERS_PARALLELISM=false, and HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1 when `offline`.
    Call before importing ML libraries in any process that runs nodes."""
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    if offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"


def configure_logging(level: str = "INFO") -> None:
    """Human-readable console logging for the CLI."""
    logging.basicConfig(
        level=_level_no(level),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
        force=True,
    )


def _dumps(line: Mapping[str, Any]) -> str:
    return json.dumps(line, ensure_ascii=False, default=str)


class RunLog:
    """Append-only JSON-lines writer for one run. Used only by the executor parent process."""

    def __init__(self, run_dir: Path, *, run_id: int, dag: str) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        self.path = run_dir / LOG_FILE
        self.run_id = run_id
        self.dag = dag
        self._lock = threading.Lock()
        self._file = self.path.open("a", encoding="utf-8")

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
        line: dict[str, Any] = {
            "ts": _now(),
            "level": _level_name(_level_no(level)),
            "kind": "event",
            "event": event,
            "message": message,
            **_tags(self.run_id, self.dag, node, version_id, doc_id),
            **fields,
        }
        self.write_lines([line])
        return line

    def write_lines(self, lines: Iterable[dict[str, Any]]) -> None:
        """Append captured lines (from a worker) as-is."""
        text = "".join(_dumps(line) + "\n" for line in lines)
        if not text:
            return
        with self._lock:
            self._file.write(text)
            self._file.flush()

    def close(self) -> None:
        with self._lock:
            self._file.close()


def read_logs(
    run_dir: Path,
    *,
    node: str | None = None,
    doc_id: str | None = None,
    level: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Lines in file order, filtered; `level` is a minimum level."""
    path = run_dir / LOG_FILE
    if not path.exists():
        return
    min_level = _level_no(level) if level else None
    with path.open(encoding="utf-8") as f:
        for raw in f:
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                continue  # partial last line after a crash
            if node is not None and line.get("node") != node:
                continue
            if doc_id is not None and line.get("doc_id") != doc_id:
                continue
            if min_level is not None and LEVELS.get(line.get("level", ""), 0) < min_level:
                continue
            yield line


def _tags(
    run_id: int, dag: str, node: str | None, version_id: int | None, doc_id: str | None
) -> dict[str, Any]:
    tags: dict[str, Any] = {"run_id": run_id, "dag": dag}
    for key, value in (("node", node), ("version_id", version_id), ("doc_id", doc_id)):
        if value is not None:
            tags[key] = value
    return tags


@dataclass
class Captured:
    lines: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False


class _Collector:
    """Thread-safe sink for captured lines that enforces the byte budget."""

    def __init__(self, captured: Captured, tags: dict[str, Any], max_bytes: int) -> None:
        self.captured = captured
        self.tags = tags
        self.max_bytes = max_bytes
        self.used = 0
        self._lock = threading.Lock()

    def add(self, kind: str, level: str, message: str, **extra: Any) -> None:
        size = len(message.encode("utf-8", "replace"))
        with self._lock:
            if self.captured.truncated:
                return
            if self.used + size > self.max_bytes:
                self.captured.truncated = True
                return
            self.used += size
            self.captured.lines.append(
                {"ts": _now(), "level": level, "kind": kind, "message": message,
                 **self.tags, **extra}
            )

    def finish(self) -> None:
        if self.captured.truncated:
            self.captured.lines.append(
                {"ts": _now(), "level": "WARNING", "kind": "log",
                 "message": f"output truncated: exceeded {self.max_bytes} bytes",
                 "logger": __name__, **self.tags}
            )


_NEWLINES = re.compile(r"\r\n|\r|\n")


class _LineSplitter:
    """Accumulates text and emits complete lines; blank lines are dropped."""

    def __init__(self, emit: Callable[[str], None]) -> None:
        self._emit = emit
        self._buf = ""
        self._lock = threading.Lock()

    def write(self, text: str) -> None:
        with self._lock:
            parts = _NEWLINES.split(self._buf + text)
            self._buf = parts.pop()
        for part in parts:
            if part.strip():
                self._emit(part)

    def flush(self) -> None:
        with self._lock:
            rest, self._buf = self._buf, ""
        if rest.strip():
            self._emit(rest)


class _StreamProxy(io.TextIOBase):
    """Stand-in for sys.stdout / sys.stderr that turns writes into captured lines."""

    def __init__(self, splitter: _LineSplitter, fd: int, encoding: str) -> None:
        self._splitter = splitter
        self._fd = fd
        self._encoding = encoding

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return self._encoding

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        return self._fd

    def write(self, s: str) -> int:
        if not isinstance(s, str):
            raise TypeError(f"write() argument must be str, not {type(s).__name__}")
        self._splitter.write(s)
        return len(s)


class _CaptureHandler(logging.Handler):
    def __init__(self, collector: _Collector, level: int) -> None:
        super().__init__(level)
        self._collector = collector
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return
        self._collector.add(
            "log", _level_name(record.levelno), message,
            logger=record.name, file=record.pathname, line=record.lineno,
        )


def _libc_fflush() -> None:
    try:
        ctypes.CDLL(None).fflush(None)
    except (OSError, AttributeError):
        pass


def _flush_std() -> None:
    for stream in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__):
        try:
            if stream is not None:
                stream.flush()
        except (OSError, ValueError):
            pass
    _libc_fflush()


@contextmanager
def _capture_fd(fd: int, splitter: _LineSplitter) -> Iterator[None]:
    """Redirect a file descriptor into a pipe drained by a reader thread."""
    try:
        saved = os.dup(fd)
    except OSError:
        yield  # fd not open in this process; nothing to capture
        return
    read_fd, write_fd = os.pipe()

    def drain() -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        with os.fdopen(read_fd, "rb", buffering=0) as pipe:
            while chunk := pipe.read(65536):
                splitter.write(decoder.decode(chunk))
        splitter.write(decoder.decode(b"", final=True))

    reader = threading.Thread(target=drain, name=f"capture-fd{fd}", daemon=True)
    reader.start()
    os.dup2(write_fd, fd)
    os.close(write_fd)
    try:
        yield
    finally:
        _flush_std()
        os.dup2(saved, fd)
        os.close(saved)
        # A child process that inherited the pipe can keep it open; don't wait for it.
        reader.join(timeout=2.0)
        splitter.flush()


@contextmanager
def _capture_logging(collector: _Collector, level: int) -> Iterator[None]:
    """Route all loggers to the collector, silencing their own handlers meanwhile."""
    root = logging.getLogger()
    handler = _CaptureHandler(collector, level)
    loggers = [root] + [
        lg for lg in list(logging.root.manager.loggerDict.values())
        if isinstance(lg, logging.Logger) and not lg.propagate
    ]
    saved = [(lg, lg.handlers[:]) for lg in loggers]
    saved_root_level = root.level
    saved_last_resort = logging.lastResort
    for lg in loggers:
        lg.handlers = [handler]
    root.setLevel(level)
    logging.lastResort = None
    try:
        yield
    finally:
        for lg, handlers in saved:
            lg.handlers = handlers
        root.setLevel(saved_root_level)
        logging.lastResort = saved_last_resort


@contextmanager
def _capture_warnings(collector: _Collector) -> Iterator[None]:
    def showwarning(
        message: Warning | str,
        category: type[Warning],
        filename: str,
        lineno: int,
        file: Any = None,
        line: str | None = None,
    ) -> None:
        collector.add(
            "warning", "WARNING", f"{category.__name__}: {message}",
            category=category.__name__, file=filename, line=lineno,
        )

    # catch_warnings restores filters and showwarning, and resets the once-per-location
    # registries so each capture sees its own warnings.
    with warnings.catch_warnings():
        warnings.showwarning = showwarning
        yield


@contextmanager
def _capture_std(name: str, splitter: _LineSplitter, fd: int) -> Iterator[None]:
    original = getattr(sys, name)
    encoding = getattr(original, "encoding", None) or "utf-8"
    setattr(sys, name, _StreamProxy(splitter, fd, encoding))
    try:
        yield
    finally:
        setattr(sys, name, original)
        splitter.flush()


@contextmanager
def capture(
    *,
    run_id: int,
    dag: str,
    node: str,
    version_id: int | None,
    doc_id: str | None,
    max_bytes: int = MAX_CAPTURE_BYTES,
    level: str = "INFO",
) -> Iterator[Captured]:
    """Capture everything a node emits while the block runs: Python logging (all loggers),
    warnings.warn, sys.stdout/sys.stderr, and native writes to file descriptors 1 and 2 (e.g. from
    C extensions), as tagged log lines. Used inside worker processes around each run() call.
    Output beyond `max_bytes` is dropped and `truncated` set (a final line says so).
    `level` is the minimum level for Python logging; stdout lines are INFO, stderr WARNING."""
    captured = Captured()
    collector = _Collector(captured, _tags(run_id, dag, node, version_id, doc_id), max_bytes)
    out = _LineSplitter(lambda s: collector.add("stdout", "INFO", s))
    err = _LineSplitter(lambda s: collector.add("stderr", "WARNING", s))
    _flush_std()
    try:
        with ExitStack() as stack:
            stack.enter_context(_capture_fd(1, out))
            stack.enter_context(_capture_fd(2, err))
            stack.enter_context(_capture_std("stdout", out, 1))
            stack.enter_context(_capture_std("stderr", err, 2))
            stack.enter_context(_capture_logging(collector, _level_no(level)))
            stack.enter_context(_capture_warnings(collector))
            yield captured
    finally:
        collector.finish()


_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_component(name: str) -> str:
    safe = _UNSAFE.sub("_", name).lstrip(".")[:200]
    return safe or "_"


def _debug_dir(run_dir: Path, node: str, doc_id: str) -> Path:
    return run_dir / DEBUG_DIR / _safe_component(node) / _safe_component(doc_id)


def debug_sink(run_dir: Path, node: str, doc_id: str) -> DebugSink:
    """A sink for NodeContext: writes run_dir/debug/<node>/<doc_id>/<name>. bytes are written as
    is, str as UTF-8, dict/list as JSON. Names are sanitized to a single path component."""
    directory = _debug_dir(run_dir, node, doc_id)

    def sink(name: str, data: Any) -> None:
        if isinstance(data, (bytes, bytearray, memoryview)):
            payload = bytes(data)
        elif isinstance(data, str):
            payload = data.encode("utf-8")
        elif isinstance(data, (Mapping, Sequence)):
            obj = dict(data) if isinstance(data, Mapping) else list(data)
            payload = json.dumps(obj, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        else:
            raise TypeError(f"unsupported debug data type {type(data).__name__}")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / _safe_component(name)).write_bytes(payload)

    return sink


def list_debug_files(run_dir: Path, node: str, doc_id: str) -> list[Path]:
    directory = _debug_dir(run_dir, node, doc_id)
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file())


def rss_mb() -> float:
    """Current process resident set size in MB."""
    return psutil.Process().memory_info().rss / (1024 * 1024)


class PeakRss:
    """Context manager that samples this process's RSS on a background thread
    (every `interval` s) and exposes the maximum as `.peak_mb` after exit."""

    def __init__(self, interval: float = 0.05) -> None:
        self.interval = interval
        self.peak_mb = 0.0
        self._stop = threading.Event()

    def __enter__(self) -> PeakRss:
        self._process = psutil.Process()
        self._stop = threading.Event()
        self.peak_mb = 0.0
        self._sample()
        self._thread = threading.Thread(target=self._loop, name="peak-rss", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        self._thread.join()
        self._sample()

    def _sample(self) -> None:
        self.peak_mb = max(self.peak_mb, self._process.memory_info().rss / (1024 * 1024))

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self._sample()
