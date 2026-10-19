from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import textwrap
import warnings
from pathlib import Path

import pytest

from ingest import obs
from ingest.contract import NodeContext

TAGS = {"run_id": 7, "dag": "default", "node": "parse", "version_id": 3, "doc_id": "abc"}


def by_kind(cap: obs.Captured, kind: str) -> list[str]:
    return [line["message"] for line in cap.lines if line["kind"] == kind]


def test_capture_logging_warnings_and_streams() -> None:
    log = logging.getLogger("thirdparty.lib")
    with obs.capture(**TAGS) as cap:
        log.info("hello %s", "world")
        log.debug("hidden")
        logging.getLogger().warning("from root")
        warnings.warn("careful", UserWarning)
        print("printed")
        print("to stderr", file=sys.stderr)
        sys.stdout.write("partial")
    assert by_kind(cap, "log") == ["hello world", "from root"]
    assert by_kind(cap, "warning") == ["UserWarning: careful"]
    assert by_kind(cap, "stdout") == ["printed", "partial"]
    assert by_kind(cap, "stderr") == ["to stderr"]
    assert not cap.truncated
    for line in cap.lines:
        assert {k: line[k] for k in TAGS} == TAGS
        assert line["ts"].endswith("Z")
    info = next(line for line in cap.lines if line["message"] == "hello world")
    assert info["level"] == "INFO" and info["logger"] == "thirdparty.lib"


def test_capture_level_and_non_propagating_logger() -> None:
    quiet = logging.getLogger("thirdparty.quiet")
    quiet.propagate = False
    try:
        with obs.capture(**TAGS, level="DEBUG") as cap:
            quiet.warning("not propagated")
            logging.getLogger("x").debug("debug line")
    finally:
        quiet.propagate = True
    assert by_kind(cap, "log") == ["not propagated", "debug line"]


def test_capture_native_fd_writes() -> None:
    with obs.capture(**TAGS) as cap:
        os.write(1, b"native out\n")
        os.write(2, b"native err\nsecond")
    assert by_kind(cap, "stdout") == ["native out"]
    assert by_kind(cap, "stderr") == ["native err", "second"]


def test_capture_in_subprocess_restores_fds(tmp_path: Path) -> None:
    script = textwrap.dedent(
        """
        import ctypes, json, os, sys
        from ingest import obs
        libc = ctypes.CDLL(None)
        with obs.capture(run_id=1, dag="d", node="n", version_id=None, doc_id="x") as cap:
            libc.printf(b"from printf\\n")
            os.write(2, b"from fd2\\n")
            print("from print")
        print(json.dumps([[l["kind"], l["message"]] for l in cap.lines]))
        os.write(2, b"after\\n")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert result.returncode == 0, result.stderr
    lines = json.loads(result.stdout.strip().splitlines()[-1])
    assert sorted(map(tuple, lines)) == sorted(
        [("stdout", "from printf"), ("stderr", "from fd2"), ("stdout", "from print")]
    )
    assert result.stdout.strip().splitlines() == [result.stdout.strip()]
    assert result.stderr == "after\n"


def test_capture_restores_state_on_exception() -> None:
    root = logging.getLogger()
    before = (root.level, root.handlers[:], sys.stdout, sys.stderr, warnings.showwarning,
              warnings.filters[:], logging.lastResort)
    fds = [os.fstat(fd).st_ino for fd in (1, 2)]
    with pytest.raises(RuntimeError, match="boom"), obs.capture(**TAGS) as cap:
        logging.getLogger("y").warning("before boom")
        warnings.simplefilter("ignore")
        raise RuntimeError("boom")
    after = (root.level, root.handlers, sys.stdout, sys.stderr, warnings.showwarning,
             warnings.filters, logging.lastResort)
    assert after == before
    assert [os.fstat(fd).st_ino for fd in (1, 2)] == fds
    assert by_kind(cap, "log") == ["before boom"]


def test_capture_logs_exception_traceback() -> None:
    with obs.capture(**TAGS) as cap:
        try:
            raise ZeroDivisionError("zero")
        except ZeroDivisionError:
            logging.getLogger("z").exception("failed")
    (message,) = by_kind(cap, "log")
    assert message.startswith("failed") and "ZeroDivisionError" in message


def test_capture_warning_once_per_capture() -> None:
    def warn() -> None:
        warnings.warn("repeated", UserWarning)

    for _ in range(2):
        with obs.capture(**TAGS) as cap:
            warn()
            warn()
        assert by_kind(cap, "warning") == ["UserWarning: repeated"]


def test_capture_truncation() -> None:
    with obs.capture(**TAGS, max_bytes=25) as cap:
        print("0123456789")
        print("0123456789")
        print("0123456789")
        os.write(1, b"more\n")
    assert cap.truncated
    assert by_kind(cap, "stdout") == ["0123456789", "0123456789"]
    last = cap.lines[-1]
    assert last["kind"] == "log" and last["level"] == "WARNING"
    assert "truncated" in last["message"]


def test_runlog_and_read_logs(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "7"
    log = obs.RunLog(run_dir, run_id=7, dag="default")
    started = log.event("run_started", message="go", nodes=["parse", "chunk"])
    assert started["kind"] == "event" and started["nodes"] == ["parse", "chunk"]
    assert "node" not in started
    log.event("doc_failed", level="error", node="parse", version_id=3, doc_id="a", error="X")
    with obs.capture(**TAGS) as cap:
        logging.getLogger("w").warning("doc warning")
        print("doc output")
    log.write_lines(cap.lines)
    log.event("doc_finished", node="chunk", version_id=4, doc_id="abc")
    log.close()
    with (run_dir / obs.LOG_FILE).open("a") as f:
        f.write('{"partial')

    events = [line.get("event") for line in obs.read_logs(run_dir)]
    assert events == ["run_started", "doc_failed", None, None, "doc_finished"]
    assert [line["message"] for line in obs.read_logs(run_dir, node="parse")] == [
        "", "doc warning", "doc output"
    ]
    assert [line.get("event") for line in obs.read_logs(run_dir, doc_id="abc")] == [
        None, None, "doc_finished"
    ]
    assert [line["level"] for line in obs.read_logs(run_dir, level="warning")] == [
        "ERROR", "WARNING"
    ]
    assert [line["message"] for line in obs.read_logs(run_dir, node="parse", doc_id="abc",
                                                      level="INFO")] == [
        "doc warning", "doc output"
    ]
    assert list(obs.read_logs(tmp_path / "missing")) == []


def test_debug_sink(tmp_path: Path) -> None:
    sink = obs.debug_sink(tmp_path, "parse", "abc")
    ctx = NodeContext(dag="d", node="parse", params={}, scratch=True, debug_sink=sink)
    ctx.debug("page-1.png", b"\x89PNG")
    ctx.debug("note.txt", "héllo")
    ctx.debug("data.json", {"a": [1, 2]})
    ctx.debug("list.json", [1, "x"])
    ctx.debug("../../escape", "x")
    ctx.debug("..", "y")
    base = tmp_path / obs.DEBUG_DIR / "parse" / "abc"
    files = obs.list_debug_files(tmp_path, "parse", "abc")
    assert all(p.parent == base for p in files)
    names = {p.name: p for p in files}
    assert names["page-1.png"].read_bytes() == b"\x89PNG"
    assert names["note.txt"].read_text("utf-8") == "héllo"
    assert json.loads(names["data.json"].read_text()) == {"a": [1, 2]}
    assert json.loads(names["list.json"].read_text()) == [1, "x"]
    assert len(files) == 6
    assert obs.list_debug_files(tmp_path, "parse", "other") == []
    with pytest.raises(TypeError):
        sink("bad", 3.5)


def test_rss_and_peak_rss() -> None:
    assert obs.rss_mb() > 1
    with obs.PeakRss(interval=0.01) as peak:
        block = bytearray(200 * 1024 * 1024)
        block[:: 4096] = b"x" * len(block[:: 4096])
        baseline = obs.rss_mb()
        del block
    assert peak.peak_mb >= baseline - 1
    assert peak.peak_mb >= 150


def test_configure_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    keys = ["HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK", "TOKENIZERS_PARALLELISM",
            "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"]
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    obs.configure_environment(offline=False)
    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"
    assert os.environ["DO_NOT_TRACK"] == "1"
    assert os.environ["TOKENIZERS_PARALLELISM"] == "false"
    assert "HF_HUB_OFFLINE" not in os.environ
    obs.configure_environment(offline=True)
    assert os.environ["HF_HUB_OFFLINE"] == "1" and os.environ["TRANSFORMERS_OFFLINE"] == "1"
