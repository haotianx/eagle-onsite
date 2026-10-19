"""`ingest` command line (Typer)."""

from __future__ import annotations

import contextlib
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, NoReturn

import typer

from ingest import obs
from ingest.config import Config, find_repo_root, load_config
from ingest.store.catalog import Catalog, EventRow, SnapshotDocRow, VersionRow

if TYPE_CHECKING:
    from ingest.engine.planner import RunPlan
    from ingest.spec import DagSpec

app = typer.Typer(no_args_is_help=True, add_completion=False, pretty_exceptions_enable=False)
models_app = typer.Typer(no_args_is_help=True)
app.add_typer(models_app, name="models", help="Model weights.")

POLL_S = 0.5
RECENT_RUNS = 5
MIN_FREE_GB = 5.0
MIN_PREFIX = 6
RECORD_LINES = 40
TABLE_PREVIEW_ROWS = 5
CELL_CHARS = 160
TERMINAL = ("succeeded", "failed", "cancelled")
NO_SETUP = "INGEST_NO_SETUP"
_HF_ID = re.compile(r"[A-Za-z0-9][\w.-]*/[\w.-]+")


@dataclass(frozen=True)
class _State:
    repo: Path | None
    log_level: str


@app.callback()
def _main(
    ctx: typer.Context,
    log_level: Annotated[str, typer.Option("--log-level", help="Console log level.")] = "INFO",
    repo: Annotated[Path | None, typer.Option("--repo", hidden=True)] = None,
) -> None:
    """Document ingestion pipeline."""
    try:
        obs.configure_logging(log_level)
    except ValueError as e:
        _fail(str(e))
    ctx.obj = _State(repo, log_level.upper())


def main() -> None:
    app()


# Helpers


def _fail(message: str) -> NoReturn:
    typer.echo(message, err=True)
    raise typer.Exit(1)


def _config(ctx: typer.Context) -> Config:
    state: _State = ctx.obj
    try:
        return load_config(find_repo_root(state.repo))
    except (FileNotFoundError, ValueError) as e:
        _fail(str(e))


@contextlib.contextmanager
def _catalog(config: Config) -> Iterator[Catalog]:
    catalog = Catalog(config.catalog_path)
    try:
        yield catalog
    finally:
        catalog.close()


def _table(rows: Sequence[Sequence[Any]], header: Sequence[str] | None = None, indent: int = 0) -> None:
    lines = [[str(c) for c in r] for r in ([header] if header else []) + list(rows)]
    if not lines:
        return
    widths = [max(len(r[i]) for r in lines) for i in range(len(lines[0]))]
    for r in lines:
        typer.echo((" " * indent + "  ".join(c.ljust(w) for c, w in zip(r, widths))).rstrip())


def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _secs(s: float | None) -> str:
    if s is None:
        return "—"
    if s < 60:
        return f"{s:.1f}s"
    return f"{int(s // 60)}m{int(s % 60):02d}s"


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _short(doc_id: str | None) -> str:
    return doc_id[:10] if doc_id else ""


def _seq(v: VersionRow) -> str:
    return f"v{v.seq}" if v.seq is not None else v.status


def _find_dag(config: Config, name: str) -> DagSpec | None:
    from ingest.spec import DagError, dag_paths, parse_dag

    for path in dag_paths(config.repo_root):
        try:
            dag = parse_dag(path, config.repo_root)
        except DagError:
            continue
        if dag.name == name:
            return dag
    return None


def _resolve_docs(rows: Sequence[SnapshotDocRow], tokens: Sequence[str]) -> list[str]:
    """Full ids, unique id prefixes (MIN_PREFIX+ chars) or job numbers → doc ids."""
    ids = [r.doc_id for r in rows]
    out: list[str] = []
    for token in tokens:
        if token in ids:
            match = [token]
        else:
            match = [r.doc_id for r in rows if r.meta.get("job_number") == token]
            if not match and len(token) >= MIN_PREFIX:
                match = [i for i in ids if i.startswith(token)]
                if len(match) > 1:
                    _fail(f"doc {token!r} is ambiguous ({len(match)} docs)")
        if not match:
            _fail(f"doc {token!r} not found (ids, {MIN_PREFIX}+ character id prefixes or job numbers)")
        out.extend(m for m in match if m not in out)
    return out


def _resolve_doc(rows: Sequence[SnapshotDocRow], token: str) -> str:
    ids = _resolve_docs(rows, [token])
    if len(ids) > 1:
        _fail(f"doc {token!r} matches {len(ids)} docs; use an id")
    return ids[0]


def _resolve_ref(catalog: Catalog, ref: str, *, promote: bool = False) -> VersionRow:
    """`dag/node@vN` exactly. A bare `dag/node` is the current version, or the newest one (with a
    note) when nothing is current; for promote it is the newest non-scratch version."""
    from ingest.publish import resolve_ref

    if "@" not in ref and ref.count("/") == 1:
        dag, node = ref.split("/")
        if promote:
            versions = catalog.list_versions(dag, node, include_scratch=False)
            if not versions:
                _fail(f"{ref} has no promotable version")
            return versions[0]
        if catalog.current_version(dag, node) is None and (versions := catalog.list_versions(dag, node)):
            typer.echo(f"{ref} has no current version; using {versions[0].label}", err=True)
            return versions[0]
    try:
        return resolve_ref(catalog, ref)
    except LookupError as e:
        _fail(str(e.args[0]) if e.args else str(e))


def _paths(catalog: Catalog, snapshot_id: int) -> dict[str, str]:
    return {r.doc_id: r.paths[0] for r in catalog.snapshot_docs(snapshot_id)}


def _issue_counts(catalog: Catalog, version_id: int) -> list[tuple[str, str, int]]:
    counts: dict[tuple[str, str], int] = {}
    for i in catalog.issues(version_id):
        counts[(i.source, i.level)] = counts.get((i.source, i.level), 0) + 1
    return [(s, lvl, n) for (s, lvl), n in sorted(counts.items())]


def _pid_alive(pid: int) -> bool:
    import psutil

    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


# sync, validate


@app.command()
def sync(
    ctx: typer.Context,
    source: Annotated[str | None, typer.Argument(help="Source name (default: all).")] = None,
) -> None:
    """Snapshot sources and show what changed."""
    from ingest.sources import sync as sync_source

    config = _config(ctx)
    if source is not None and source not in config.sources:
        _fail(f"unknown source {source!r} (known: {', '.join(config.sources)})")
    failed = False
    with _catalog(config) as catalog:
        for name in [source] if source else list(config.sources):
            try:
                result = sync_source(catalog, config.sources[name])
            except (OSError, ValueError) as e:
                typer.echo(f"✗ {name}: {e}")
                failed = True
                continue
            snap = catalog.get_snapshot(result.snapshot_id)
            state = "new" if result.created else "unchanged"
            typer.echo(f"{name}  snapshot {snap.id} ({state}) · {snap.doc_count} docs · {snap.file_count} files")
            d = result.diff
            typer.echo(
                f"  added {len(d.added)} · removed {len(d.removed)} · changed paths {len(d.changed_paths)}"
                f" · unchanged {d.unchanged}"
            )
            flagged = [r for r in catalog.snapshot_docs(snap.id) if r.flags]
            if flagged:
                typer.echo("  flagged")
                _table([(",".join(r.flags), r.paths[0], r.flag_reason or "") for r in flagged], indent=4)
    if failed:
        raise typer.Exit(1)


@app.command()
def validate(ctx: typer.Context) -> None:
    """Validate every DAG."""
    from ingest.spec import DagError, load_dags

    config = _config(ctx)
    try:
        dags = load_dags(config.repo_root, config.sources)
    except DagError as e:
        for err in e.errors:
            typer.echo(f"✗ {err}")
        raise typer.Exit(1) from None
    for name, dag in sorted(dags.items()):
        typer.echo(f"✓ {name}  {dag.path} · {len(dag.nodes)} nodes")


# run, cancel


@app.command()
def run(
    ctx: typer.Context,
    dag: Annotated[str, typer.Argument(help="DAG name.")],
    node: Annotated[str | None, typer.Option("--node", help="Only this node.")] = None,
    from_: Annotated[str | None, typer.Option("--from", help="This node and downstream.")] = None,
    to: Annotated[str | None, typer.Option("--to", help="This node and upstream.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Recompute cached docs.")] = False,
    sample: Annotated[int | None, typer.Option("--sample", help="Random sample of N docs.")] = None,
    doc: Annotated[
        list[str] | None, typer.Option("--doc", help="Doc id, id prefix or job number (repeatable).")
    ] = None,
    snapshot: Annotated[int | None, typer.Option("--snapshot", help="Snapshot id (default: latest).")] = None,
    input_: Annotated[
        list[str] | None, typer.Option("--input", help="NODE=vN input version (repeatable).")
    ] = None,
    code: Annotated[str, typer.Option("--code", help="auto, committed or working.")] = "auto",
    online: Annotated[bool, typer.Option("--online", help="Let workers download.")] = False,
    log_level: Annotated[str | None, typer.Option("--log-level", help="Run log level.")] = None,
    background: Annotated[bool, typer.Option("--background", help="Print the run id and return.")] = False,
) -> None:
    """Plan and run a DAG."""
    from ingest.engine.executor import submit
    from ingest.engine.planner import PlanError, RunRequest, plan_run

    config = _config(ctx)
    if sum(x is not None for x in (node, from_, to)) > 1:
        _fail("use one of --node, --from, --to")
    if sample is not None and doc:
        _fail("use --sample or --doc, not both")
    if code not in ("auto", "committed", "working"):
        _fail(f"unknown --code {code!r} (expected auto, committed or working)")
    level = (log_level or ctx.obj.log_level).upper()
    if level not in obs.LEVELS:
        _fail(f"unknown log level {level!r}")
    selection, target = (
        ("node", node) if node else ("downstream", from_) if from_ else ("upstream", to) if to else ("all", None)
    )
    with _catalog(config) as catalog:
        docs: Any = "all" if sample is None else sample
        if doc:
            spec = _find_dag(config, dag)
            if spec is None:
                _fail(f"unknown DAG {dag!r}")
            snap = snapshot if snapshot is not None else getattr(catalog.latest_snapshot(spec.source), "id", None)
            if snap is None:
                _fail(f"source {spec.source!r} has no snapshot; sync it first")
            docs = tuple(_resolve_docs(catalog.snapshot_docs(snap), doc))
        inputs: dict[str, int] = {}
        for item in input_ or []:
            name, _, ver = item.partition("=")
            seq = ver.removeprefix("v")
            if not name or not seq.isdigit():
                _fail(f"--input {item!r}: expected NODE=vN")
            v = catalog.find_version(dag, name, int(seq))
            if v is None:
                _fail(f"version {dag}/{name}@v{seq} not found")
            inputs[name] = v.id
        request = RunRequest(
            dag=dag,
            target=target,
            selection=selection,  # type: ignore[arg-type]
            snapshot_id=snapshot,
            input_versions=inputs,
            recompute="force" if force else "reuse",
            docs=docs,
            code=code,  # type: ignore[arg-type]
        )
        try:
            plan = plan_run(catalog, config, request)
            _print_plan(plan)
            run_id = submit(config, request, online=online, log_level=level)
        except PlanError as e:
            _fail(str(e))
        if background:
            typer.echo(f"run {run_id}")
            return
        status = _follow(catalog, run_id, {n.node: n.docs_total for n in plan.nodes})
        _summary(catalog.events(run_id))
    if status != "succeeded":
        raise typer.Exit(1)


def _print_plan(plan: RunPlan) -> None:
    code = plan.code + (f" {plan.commit[:10]}" if plan.commit else "") + (" (dirty)" if plan.dirty else "")
    typer.echo(f"{plan.dag.name}  snapshot {plan.snapshot_id} · {len(plan.doc_ids)} docs · {code}")
    _table(
        [(n.node, n.docs_to_process, n.docs_cached, n.docs_blocked) for n in plan.nodes],
        ("node", "to process", "cached", "blocked"),
    )
    typer.echo(f"Scratch: {', '.join(plan.scratch_reasons)}" if plan.scratch else "Promotable")


def _follow(catalog: Catalog, run_id: int, totals: dict[str, int]) -> str:
    """Print progress from the run's events until it ends; Ctrl-C requests cancel."""
    from rich.console import Console
    from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

    from ingest.engine.executor import cancel

    console = Console(markup=False, highlight=False)
    progress = Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
        disable=not console.is_terminal,
    )
    tasks: dict[str, Any] = {}
    pid: int | None = None
    after = 0
    cancelling = False

    def say(line: str) -> None:
        if console.is_terminal:
            progress.console.print(line)
        else:
            typer.echo(line)

    with progress:
        while True:
            try:
                for e in catalog.events(run_id, after):
                    after = e.id
                    pid = e.payload.get("executor_pid", pid)
                    node = e.payload.get("node")
                    if e.kind == "node_started" and node:
                        tasks[node] = progress.add_task(node, total=totals.get(node))
                    elif e.kind.startswith("doc_") and node in tasks:
                        progress.advance(tasks[node])
                    elif e.kind in ("node_finished", "node_failed", "node_skipped") and node in tasks:
                        progress.remove_task(tasks.pop(node))
                    if (line := _event_line(e)) is not None:
                        say(line)
                    if e.kind == "run_finished":
                        return e.payload.get("status", "failed")
                r = catalog.get_run(run_id)
                if r.status in TERMINAL and not catalog.events(run_id, after):
                    say(f"run {run_id} {r.status}" + (f": {r.error}" if r.error else ""))
                    return r.status
                if pid is not None and not _pid_alive(pid) and not catalog.events(run_id, after):
                    r = catalog.get_run(run_id)
                    if r.status not in TERMINAL:
                        say(f"run {run_id}: executor process exited")
                        return "failed"
                time.sleep(POLL_S)
            except KeyboardInterrupt:
                if cancelling:
                    say(f"run {run_id} continues in the background")
                    raise typer.Exit(1) from None
                cancelling = True
                cancel(catalog, run_id)
                say(f"cancelling run {run_id}")


def _event_line(e: EventRow) -> str | None:
    p = e.payload
    node = p.get("node", "")
    if e.kind == "run_started":
        return f"run {e.run_id} started"
    if e.kind == "run_waiting":
        return f"run {e.run_id} waits for run(s) {', '.join(map(str, p.get('blocking', [])))}"
    if e.kind == "node_started":
        return f"{node}  started"
    if e.kind == "doc_failed":
        return f"  ✗ {node} {_short(p.get('doc_id'))}  {p.get('error_type')}: {p.get('error')}"
    if e.kind == "node_finished":
        reused = " (reused)" if p.get("reused") else ""
        return (
            f"{node}  {p.get('version')}{reused} · ok {p.get('ok', 0)} · error {p.get('error', 0)}"
            f" · skipped {p.get('skipped', 0)} · cached {p.get('cached', 0)} · {_secs(p.get('wall_s'))}"
        )
    if e.kind == "node_failed":
        return f"✗ {node}: {p.get('error')}"
    if e.kind == "node_skipped":
        return f"{node}  not run"
    if e.kind == "run_finished":
        return f"run {e.run_id} {p.get('status')}" + (f": {p['error']}" if p.get("error") else "")
    return None


def _summary(events: Sequence[EventRow]) -> None:
    rows = []
    for e in events:
        p = e.payload
        if e.kind == "node_finished":
            version = str(p.get("version")) + (" (reused)" if p.get("reused") else "")
            rows.append(
                (p["node"], p.get("processed", 0), p.get("cached", 0), p.get("error", 0), p.get("skipped", 0),
                 _secs(p.get("wall_s")), version)
            )
        elif e.kind in ("node_failed", "node_skipped"):
            rows.append((p["node"], "—", "—", "—", "—", "—", "failed" if e.kind == "node_failed" else "not run"))
    if rows:
        _table(rows, ("node", "processed", "cached", "failed", "skipped", "wall", "version"))


@app.command("cancel")
def cancel_cmd(ctx: typer.Context, run_id: Annotated[int, typer.Argument(metavar="RUN")]) -> None:
    """Cancel a run."""
    from ingest.engine.executor import cancel

    config = _config(ctx)
    with _catalog(config) as catalog:
        try:
            r = catalog.get_run(run_id)
        except KeyError:
            _fail(f"run {run_id} not found")
        if r.status in TERMINAL:
            _fail(f"run {run_id} is {r.status}")
        cancel(catalog, run_id)
    typer.echo(f"run {run_id}: cancel requested")


# status, show, diff


@app.command()
def status(
    ctx: typer.Context, dag: Annotated[str | None, typer.Argument(help="DAG name (default: all).")] = None
) -> None:
    """Node states and recent runs."""
    from ingest.spec import DagError, dag_paths, load_dag
    from ingest.state import node_states

    config = _config(ctx)
    specs: list[DagSpec] = []
    failed = False
    for path in dag_paths(config.repo_root):
        try:
            spec = load_dag(path, config.repo_root, config.sources)
        except DagError as e:
            if dag is None:
                for err in e.errors:
                    typer.echo(f"✗ {err}")
                failed = True
            continue
        if dag is None or spec.name == dag:
            specs.append(spec)
    if dag is not None and not specs:
        _fail(f"unknown or invalid DAG {dag!r}")
    with _catalog(config) as catalog:
        for i, spec in enumerate(specs):
            if i:
                typer.echo()
            typer.echo(f"{spec.name}  source {spec.source}")
            states = node_states(catalog, config, spec).values()
            dirty = any(s.dirty for s in states)
            rows = [
                (
                    s.node,
                    s.state.replace("_", " "),
                    _seq(s.current) if s.current else "—",
                    " ".join(_seq(c) for c in s.candidates) or "—",
                    len(s.scratch),
                    *(("uncommitted" if s.dirty else "",) if dirty else ()),
                    "; ".join(s.reasons),
                )
                for s in states
            ]
            _table(rows, ("node", "state", "current", "candidates", "scratch", *(("git",) if dirty else ()), "reasons"))
            runs = catalog.list_runs(spec.name, limit=RECENT_RUNS)
            if runs:
                typer.echo("runs")
                now = datetime.now(UTC)
                _table(
                    [
                        (
                            r.id,
                            r.status,
                            "scratch" if r.scratch else "",
                            ",".join(r.request.get("nodes") or []),
                            _secs(((_ts(r.ended_at) if r.ended_at else now) - _ts(r.started_at)).total_seconds())
                            if r.started_at
                            else "—",
                        )
                        for r in runs
                    ],
                    indent=2,
                )
    if failed:
        raise typer.Exit(1)


def _checks_changed(config: Config, catalog: Catalog, v: VersionRow) -> bool:
    """Whether the version's checks file now fingerprints differently from its stored issues."""
    from ingest.fingerprint import checks_fingerprint, sha256_file

    checks = v.meta.get("checks")
    if not checks:
        return False
    path = config.repo_root / checks
    if not path.is_file():
        return True
    stored = [i.checks_fp for i in catalog.issues(v.id) if i.source == "script"]
    recorded = stored[-1] if stored else v.meta.get("checks_fp")
    return checks_fingerprint(sha256_file(path), v.meta.get("params") or {}) != recorded


@app.command()
def show(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument(help="dag/node or dag/node@vN.")],
    doc: Annotated[str | None, typer.Option("--doc", help="Doc id, id prefix or job number.")] = None,
) -> None:
    """Show a version, or one doc in it."""
    from ingest.state import promotable

    config = _config(ctx)
    with _catalog(config) as catalog:
        v = _resolve_ref(catalog, ref)
        if doc is not None:
            _show_doc(config, catalog, v, _resolve_doc(catalog.snapshot_docs(v.snapshot_id), doc))
            return
        from ingest.metrics import version_summary
        from ingest.store.objects import ObjectStore

        current = catalog.current_version(v.dag, v.node)
        ok, reasons = promotable(catalog, v.id)
        flags = [v.status]
        if v.scratch:
            flags.append("scratch")
        elif current is not None and current.id == v.id:
            flags.append("current")
        elif ok:
            flags.append("promotable")
        typer.echo(f"{v.label}  {' · '.join(flags)}")
        inputs = ", ".join(f"{n}: {catalog.get_version(i).label}" for n, i in sorted(v.input_versions.items()))
        meta = v.meta
        _table(
            [
                ("run", v.run_id if v.run_id is not None else "—"),
                ("created", v.created_at[:19].replace("T", " ")),
                ("snapshot", v.snapshot_id),
                ("inputs", inputs or "—"),
                ("commit", (v.commit[:12] if v.commit else "—") + (" (dirty)" if v.dirty else "")),
                ("code", meta.get("code", "—")),
                ("fingerprint", v.node_fp[:16]),
                ("script", meta.get("script", "—")),
                ("params", json.dumps(meta.get("params") or {}, sort_keys=True)),
                ("checks", meta.get("checks") or "—"),
            ],
            indent=2,
        )
        summary = version_summary(catalog, ObjectStore(config.objects_dir), v.id)
        parts = [f"{k} {_size(val) if k == 'size_bytes' else _fmt(val)}" for k, val in summary.items()]
        typer.echo("summary  " + " · ".join(p.replace("size_bytes", "size") for p in parts))
        counts = _issue_counts(catalog, v.id)
        if counts:
            typer.echo("issues")
            _table(counts, ("source", "level", "count"), indent=2)
        paths = _paths(catalog, v.snapshot_id)
        failures = [d for d in catalog.version_docs(v.id) if d.status == "error"]
        if failures:
            typer.echo("failures")
            _table(
                [(_short(d.doc_id), paths.get(d.doc_id, ""), f"{d.error_type}: {d.error_message}") for d in failures],
                indent=2,
            )
        if _checks_changed(config, catalog, v):
            typer.echo(f"checks file changed: revalidate {v.label}")


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.1f}"
    return "—" if value is None else str(value)


def _show_doc(config: Config, catalog: Catalog, v: VersionRow, doc_id: str) -> None:
    from ingest.store.objects import ObjectStore

    row = catalog.snapshot_doc(v.snapshot_id, doc_id)
    typer.echo(f"{v.label}  doc {doc_id}")
    if row is not None:
        typer.echo(f"  {row.paths[0]}" + "".join(f"\n  {p}" for p in row.paths[1:]))
    d = catalog.version_doc(v.id, doc_id)
    if d is None:
        typer.echo("not in this version")
        return
    typer.echo(f"status  {d.status}" + (" (cached)" if d.cached else ""))
    if d.error_type or d.error_message:
        typer.echo(f"error   {d.error_type}: {d.error_message}")
    timing = [_secs(d.duration_s)]
    if d.peak_rss_mb is not None:
        timing.append(f"peak {d.peak_rss_mb:.0f} MB")
    if d.worker_pid is not None:
        timing.append(f"pid {d.worker_pid}")
    typer.echo("timing  " + " · ".join(timing))
    if d.traceback:
        typer.echo("traceback")
        typer.echo("\n".join("  " + line for line in d.traceback.rstrip().splitlines()))
    issues = catalog.issues(v.id, doc_id)
    if issues:
        typer.echo("issues")
        _table(
            [(i.source, i.level, i.code, f"p{i.page}" if i.page is not None else "", i.message) for i in issues],
            indent=2,
        )
    if d.object_sha and d.kind:
        record = ObjectStore(config.objects_dir).get(d.object_sha, d.kind)  # type: ignore[arg-type]
        typer.echo("record")
        for line in _record_lines(record):
            typer.echo("  " + line)
    if v.run_id is not None:
        files = obs.list_debug_files(config.run_dir(v.run_id), v.node, doc_id)
        if files:
            typer.echo("debug files")
            for f in files:
                typer.echo(f"  {f}")


def _clip(text: str, n: int = CELL_CHARS) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _record_lines(record: Any) -> list[str]:
    if isinstance(record, dict):
        lines = json.dumps(record, indent=2, ensure_ascii=False, default=str).splitlines()
        more = len(lines) - RECORD_LINES
        return [_clip(x) for x in lines[:RECORD_LINES]] + ([f"… {more} more lines"] if more > 0 else [])
    rows = record.slice(0, TABLE_PREVIEW_ROWS).to_pylist()
    out = [f"{record.num_rows} rows × {record.num_columns} columns: {', '.join(record.column_names)}"]
    out += [_clip(json.dumps(r, ensure_ascii=False, default=str)) for r in rows]
    if record.num_rows > TABLE_PREVIEW_ROWS:
        out.append(f"… {record.num_rows - TABLE_PREVIEW_ROWS} more rows")
    return out


@app.command()
def diff(
    ctx: typer.Context,
    a: Annotated[str, typer.Argument(help="Version ref A.")],
    b: Annotated[str, typer.Argument(help="Version ref B.")],
    doc: Annotated[str | None, typer.Option("--doc", help="Doc id, id prefix or job number.")] = None,
    limit: Annotated[int, typer.Option("--limit", help="Changed docs to list.")] = 10,
) -> None:
    """Compare two versions."""
    from ingest.metrics import compare_versions, doc_diff
    from ingest.store.objects import ObjectStore

    config = _config(ctx)
    objects = ObjectStore(config.objects_dir)
    with _catalog(config) as catalog:
        va, vb = _resolve_ref(catalog, a), _resolve_ref(catalog, b)
        if doc is not None:
            rows = {r.doc_id: r for s in (va.snapshot_id, vb.snapshot_id) for r in catalog.snapshot_docs(s)}
            doc_id = _resolve_doc(list(rows.values()), doc)
            typer.echo(f"{va.label} → {vb.label}  doc {doc_id}")
            _print_doc_diff(doc_diff(catalog, objects, va.id, vb.id, doc_id))
            return
        cmp = compare_versions(catalog, objects, va.id, vb.id)
        keys = list(dict.fromkeys([*cmp.a, *cmp.b]))
        _table(
            [
                (k.replace("size_bytes", "size"), *(
                    _size(s[k]) if k == "size_bytes" and k in s else _fmt(s.get(k)) for s in (cmp.a, cmp.b)
                ))
                for k in keys
            ],
            ("", va.label, vb.label),
        )
        typer.echo(f"changed {len(cmp.changed)} · unchanged {cmp.unchanged}")
        if cmp.changed:
            paths = _paths(catalog, vb.snapshot_id) | _paths(catalog, va.snapshot_id)
            _table(
                [
                    (_short(c.doc_id), _fmt(c.a_records), _fmt(c.b_records), _fmt(c.change), paths.get(c.doc_id, ""))
                    for c in cmp.changed[:limit]
                ],
                ("doc", "a", "b", "change", "path"),
                indent=2,
            )


def _print_doc_diff(d: dict[str, Any]) -> None:
    a, b = d["a"], d["b"]
    if "only_a" in d:
        typer.echo(
            f"a {len(a) if a is not None else '—'} rows · b {len(b) if b is not None else '—'} rows"
            f" · only a {len(d['only_a'])} · only b {len(d['only_b'])}"
        )
        for sign, side, hashes in (("-", a, d["only_a"]), ("+", b, d["only_b"])):
            wanted = set(hashes)
            for r in side or []:
                if r.get("text_hash") in wanted:
                    wanted.discard(r.get("text_hash"))
                    typer.echo(f"{sign} {_clip(' '.join(str(r.get('text', '')).split()))}")
        return
    if a == b:
        typer.echo("identical")
        return
    dump = lambda x: [] if x is None else json.dumps(x, indent=2, sort_keys=True, default=str).splitlines()  # noqa: E731
    lines = list(difflib.unified_diff(dump(a), dump(b), "a", "b", lineterm="", n=2))
    for line in lines[: RECORD_LINES * 2]:
        typer.echo(_clip(line))
    if len(lines) > RECORD_LINES * 2:
        typer.echo(f"… {len(lines) - RECORD_LINES * 2} more lines")


# promote, revalidate


@app.command()
def promote(
    ctx: typer.Context,
    ref: Annotated[str, typer.Argument(help="dag/node (newest candidate) or dag/node@vN.")],
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation.")] = False,
) -> None:
    """Make a version current."""
    from ingest.state import promotable
    from ingest.state import promote as do_promote

    config = _config(ctx)
    with _catalog(config) as catalog:
        v = _resolve_ref(catalog, ref, promote=True)
        ok, reasons = promotable(catalog, v.id)
        if not ok:
            _fail(f"{v.label} is not promotable: {'; '.join(reasons)}")
        current = catalog.current_version(v.dag, v.node)
        typer.echo(f"promote {v.label}, replacing {current.label if current else 'nothing (no current version)'}")
        if not yes:
            counts = _issue_counts(catalog, v.id)
            if counts:
                _table(counts, ("source", "level", "count"), indent=2)
            for name, used in sorted(v.input_versions.items()):
                cur = catalog.current_version(v.dag, name)
                if cur is None or cur.id != used:
                    typer.echo(
                        f"! built from {catalog.get_version(used).label}, but the current {v.dag}/{name}"
                        f" is {cur.label if cur else 'none'}"
                    )
            typer.confirm("Promote?", abort=True)
        published = config.published_dir / v.dag / f"{v.node}.parquet"
        before = published.stat().st_mtime_ns if published.exists() else None
        try:
            warnings = do_promote(catalog, config, v.id)
        except ValueError as e:
            _fail(str(e))
        if yes:
            for w in warnings:
                typer.echo(f"! {w}")
        typer.echo(f"{v.label} is current")
        if published.exists() and published.stat().st_mtime_ns != before:
            typer.echo(f"published {published}")


@app.command()
def revalidate(ctx: typer.Context, ref: Annotated[str, typer.Argument(help="dag/node or dag/node@vN.")]) -> None:
    """Re-run a version's checks file and replace its issues."""
    from ingest.fingerprint import checks_fingerprint
    from ingest.scripts import ScriptError, load_checks
    from ingest.store.objects import ObjectStore
    from ingest.validation import revalidate as do_revalidate

    config = _config(ctx)
    with _catalog(config) as catalog:
        v = _resolve_ref(catalog, ref)
        params = dict(v.meta.get("params") or {})
        checks, fp = None, None
        if path := v.meta.get("checks"):
            try:
                checks = load_checks(config.repo_root / path)
            except ScriptError as e:
                _fail(str(e))
            fp = checks_fingerprint(checks.source_hash, params)
        n = do_revalidate(catalog, ObjectStore(config.objects_dir), v.id, checks, params, fp)
    typer.echo(f"{v.label}: {n} issue(s)")


# logs


@app.command()
def logs(
    ctx: typer.Context,
    run_id: Annotated[int, typer.Argument(metavar="RUN")],
    node: Annotated[str | None, typer.Option("--node", help="Only this node.")] = None,
    doc: Annotated[str | None, typer.Option("--doc", help="Doc id, id prefix or job number.")] = None,
    level: Annotated[str | None, typer.Option("--level", help="Minimum level.")] = None,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Tail until the run ends.")] = False,
) -> None:
    """Show a run's logs."""
    config = _config(ctx)
    if level is not None and level.upper() not in obs.LEVELS:
        _fail(f"unknown log level {level!r}")
    with _catalog(config) as catalog:
        try:
            r = catalog.get_run(run_id)
        except KeyError:
            _fail(f"run {run_id} not found")
        doc_id = None
        if doc is not None:
            versions = catalog.run_versions(run_id)
            snap = versions[0].snapshot_id if versions else r.request.get("snapshot_id")
            if snap is None:
                spec = _find_dag(config, r.dag)
                latest = catalog.latest_snapshot(spec.source) if spec else None
                snap = latest.id if latest else None
            doc_id = _resolve_doc(catalog.snapshot_docs(snap), doc) if snap is not None else doc
        run_dir = config.run_dir(run_id)
        shown = 0
        try:
            while True:
                done = catalog.get_run(run_id).status in TERMINAL
                lines = list(obs.read_logs(run_dir, node=node, doc_id=doc_id, level=level))
                for line in lines[shown:]:
                    typer.echo(_log_line(line))
                shown = len(lines)
                if not follow or done:
                    return
                time.sleep(POLL_S)
        except KeyboardInterrupt:
            return


def _log_line(line: dict[str, Any]) -> str:
    ts = str(line.get("ts", ""))[11:19]
    kind = line.get("kind")
    message = line.get("message") or ""
    if kind == "event":
        if not message:
            extra = [f"{k}={line[k]}" for k in ("status", "error_type", "error", "reason") if line.get(k)]
            if line.get("duration_s") is not None:
                extra.append(_secs(line["duration_s"]))
            message = " ".join([str(line.get("event")), *extra])
    elif kind in ("stdout", "stderr", "warning"):
        message = f"[{kind}] {message}"
    tags = " ".join(t for t in (line.get("node"), _short(line.get("doc_id"))) if t)
    return f"{ts} {line.get('level', ''):<7} {tags + '  ' if tags else ''}{message}"


# doctor, models pull, ui

_PROBE = """
import json, sys
from ingest.obs import configure_environment
configure_environment(offline=True)
out = {}
try:
    import torch
    out["mps"] = [bool(torch.backends.mps.is_available()), "torch " + torch.__version__]
except Exception as e:
    out["mps"] = [False, f"{type(e).__name__}: {e}"]
try:
    import ocrmac.ocrmac
    out["ocrmac"] = [True, ""]
except Exception as e:
    out["ocrmac"] = [False, f"{type(e).__name__}: {e}"]
if "--docling" in sys.argv:
    try:
        from huggingface_hub import snapshot_download
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.models.stages.table_structure.table_structure_model import TableStructureModel
        spec = PdfPipelineOptions().layout_options.model_spec
        for name, fetch in (
            (f"docling layout {spec.repo_id}@{spec.revision}", lambda: snapshot_download(spec.repo_id, revision=spec.revision)),
            ("docling tableformer", TableStructureModel.download_models),
        ):
            try:
                fetch()
                out[name] = [True, ""]
            except Exception as e:
                out[name] = [False, f"not in the local cache ({type(e).__name__})"]
    except Exception as e:
        out["docling models"] = [False, f"{type(e).__name__}: {e}"]
print(json.dumps(out))
"""

_PULL = """
import json, sys
from pathlib import Path
from ingest.obs import configure_environment
configure_environment(offline=False)
from ingest.scripts import load_script
script = load_script(Path(sys.argv[1]))
if script.setup is None:
    print(%r)
else:
    script.setup(json.loads(sys.argv[2]))
""" % NO_SETUP


def _hf_models(params: dict[str, Any]) -> list[tuple[str, str | None]]:
    """(repo id, revision) pairs named in node params: `<key>: org/name` with `<key>_revision`,
    or `revision` for `model`."""
    out = []
    for key, value in params.items():
        if isinstance(value, str) and _HF_ID.fullmatch(value) and not key.endswith("revision"):
            rev = params.get(f"{key}_revision", params.get("revision") if key == "model" else None)
            out.append((value, rev if isinstance(rev, str) else None))
    return out


def _hf_cached(repos: dict[str, Any], repo_id: str, revision: str | None) -> bool:
    repo = repos.get(repo_id)
    if repo is None:
        return False
    rev = revision or "main"
    return any(r.commit_hash == rev or rev in r.refs or (len(rev) >= 7 and r.commit_hash.startswith(rev))
               for r in repo.revisions)


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Check the environment."""
    from ingest.spec import DagError, load_dags

    config = _config(ctx)
    failed = False

    def check(ok: bool | None, text: str) -> None:
        nonlocal failed
        failed = failed or ok is False
        typer.echo(f"{'✓' if ok else '!' if ok is None else '✗'} {text}")

    v = sys.version_info
    check(v[:2] == (3, 13), f"Python {v.major}.{v.minor}.{v.micro}")

    for name, source in config.sources.items():
        folders = getattr(source, "folders", (source.root,))
        for i, folder in enumerate(folders):
            if i and not folder.exists():
                continue
            readable = folder.is_dir() and os.access(folder, os.R_OK | os.X_OK)
            check(readable, f"source {name}: {folder}" + ("" if readable else " not readable"))

    dags: dict[str, DagSpec] = {}
    try:
        dags = load_dags(config.repo_root, config.sources)
        check(True, f"DAGs valid ({', '.join(sorted(dags)) or 'none'})")
    except DagError as e:
        check(False, "DAGs invalid: " + "; ".join(e.errors))

    try:
        Catalog(config.catalog_path).close()
        check(True, f"catalog {config.catalog_path}")
    except Exception as e:
        check(False, f"catalog {config.catalog_path}: {e}")

    probe_dir = config.data_dir
    while not probe_dir.exists():
        probe_dir = probe_dir.parent
    free_gb = shutil.disk_usage(probe_dir).free / 1e9
    check(True if free_gb >= MIN_FREE_GB else None, f"free disk {free_gb:.1f} GB in {probe_dir}")

    wanted: dict[tuple[str, str | None], list[str]] = {}
    docling: list[str] = []
    for dag in dags.values():
        for name, node in dag.nodes.items():
            for m in _hf_models(dict(node.params)):
                wanted.setdefault(m, []).append(f"{dag.name}/{name}")
            if "DocumentConverter" in (config.repo_root / node.script).read_text(encoding="utf-8"):
                docling.append(f"{dag.name}/{name}")

    args = [sys.executable, "-c", _PROBE] + (["--docling"] if docling else [])
    try:
        proc = subprocess.run(args, cwd=config.repo_root, capture_output=True, text=True, timeout=300)
        probe: dict[str, list[Any]] = json.loads(proc.stdout.strip().splitlines()[-1])
    except (subprocess.SubprocessError, ValueError, IndexError) as e:
        probe = {"mps": [False, f"probe failed: {e}"], "ocrmac": [False, "probe failed"]}
    mps_ok, mps_info = probe.pop("mps")
    check(mps_ok, f"torch MPS {'available' if mps_ok else 'not available'} ({mps_info})")
    ocr_ok, ocr_info = probe.pop("ocrmac")
    check(ocr_ok, "ocrmac importable" if ocr_ok else f"ocrmac not importable: {ocr_info}")
    for name, (ok, info) in probe.items():
        check(ok, f"{name} ({', '.join(docling)})" + (f": {info}" if info else ""))

    if wanted:
        from huggingface_hub import scan_cache_dir
        from huggingface_hub.errors import CacheNotFound

        try:
            repos = {r.repo_id: r for r in scan_cache_dir().repos if r.repo_type == "model"}
        except CacheNotFound:
            repos = {}
        for (repo_id, rev), users in wanted.items():
            ok = _hf_cached(repos, repo_id, rev)
            label = f"{repo_id}@{rev[:12] if rev else 'main'}"
            check(ok, f"model {label} ({', '.join(users)})" + ("" if ok else ": not in the local cache"))
    if failed:
        raise typer.Exit(1)


@models_app.command("pull")
def models_pull(
    ctx: typer.Context, dag: Annotated[str | None, typer.Argument(help="DAG name (default: all).")] = None
) -> None:
    """Download the models each node's setup() needs."""
    from ingest.fingerprint import canonical_json
    from ingest.spec import DagError, load_dags

    config = _config(ctx)
    try:
        dags = load_dags(config.repo_root, config.sources)
    except DagError as e:
        _fail("\n".join(f"✗ {err}" for err in e.errors))
    if dag is not None and dag not in dags:
        _fail(f"unknown DAG {dag!r}")
    env = {k: v for k, v in os.environ.items() if k not in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")}
    seen: dict[tuple[str, str], str] = {}
    failed = False
    for spec in [dags[dag]] if dag else [dags[n] for n in sorted(dags)]:
        for name in spec.topo_order():
            node = spec.nodes[name]
            label = f"{spec.name}/{name}"
            key = (node.script, canonical_json(dict(node.params)))
            if key in seen:
                typer.echo(f"✓ {label}  same as {seen[key]}")
                continue
            seen[key] = label
            start = time.perf_counter()
            proc = subprocess.run(
                [sys.executable, "-c", _PULL, str(config.repo_root / node.script), json.dumps(dict(node.params))],
                cwd=config.repo_root,
                capture_output=True,
                text=True,
                env=env,
            )
            wall = _secs(time.perf_counter() - start)
            if proc.returncode != 0:
                failed = True
                last = (proc.stderr.strip().splitlines() or [f"exit code {proc.returncode}"])[-1]
                typer.echo(f"✗ {label}  {last}")
            elif NO_SETUP in proc.stdout:
                typer.echo(f"✓ {label}  no setup")
            else:
                typer.echo(f"✓ {label}  {wall}")
    if failed:
        raise typer.Exit(1)


@app.command()
def ui(
    ctx: typer.Context,
    host: Annotated[str | None, typer.Option("--host", help="Bind address.")] = None,
    port: Annotated[int | None, typer.Option("--port", help="Port.")] = None,
) -> None:
    """Start the web UI."""
    from ingest.api.app import DEFAULT_HOST, DEFAULT_PORT, serve

    config = _config(ctx)
    try:
        serve(config, host=host or DEFAULT_HOST, port=port or DEFAULT_PORT)
    except NotImplementedError:
        _fail("The UI isn't available yet.")
