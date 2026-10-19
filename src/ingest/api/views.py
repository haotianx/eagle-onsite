"""Catalog rows → API models, and working-tree DAG loading shared by the routes."""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from ingest import fingerprint, metrics, sources, spec, state
from ingest.api.models import (
    InputVersion,
    IssueCounts,
    RunSummary,
    SnapshotOut,
    VersionDetail,
    VersionSummary,
)
from ingest.config import Config
from ingest.store.catalog import Catalog, IssueRow, RunRow, SnapshotDocRow, SnapshotRow, VersionRow
from ingest.store.objects import ObjectStore

if TYPE_CHECKING:
    import pyarrow as pa

TERMINAL = ("succeeded", "failed", "cancelled")


# Working-tree DAGs


@dataclass(frozen=True)
class DagFile:
    path: Path
    rel: str
    name: str
    """The DAG's name, or the file stem when it can't be parsed."""
    dag: spec.DagSpec | None
    errors: list[str]


def dag_files(config: Config) -> list[DagFile]:
    """Every dags/*.yaml, parsed and validated separately."""
    root = config.repo_root
    out = []
    for path in spec.dag_paths(root):
        rel = path.relative_to(root).as_posix()
        try:
            dag = spec.parse_dag(path, root)
        except spec.DagError as e:
            out.append(DagFile(path, rel, path.stem, None, list(e.errors)))
            continue
        out.append(DagFile(path, rel, dag.name, dag, spec.validate_dag(dag, root, config.sources)))
    return out


def find_dag(config: Config, name: str) -> DagFile | None:
    return next((f for f in dag_files(config) if f.name == name), None)


def node_dirty(dag: spec.DagSpec, node: str, dirty: set[str]) -> bool:
    return any(f in dirty for f in dag.node_files(node))


def artifact_type(script: Path) -> str | None:
    """A module-level `ARTIFACT_TYPE = "..."` literal, read without importing the script."""
    try:
        tree = ast.parse(script.read_bytes())
    except (OSError, SyntaxError, ValueError):
        return None
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            targets, value = stmt.targets, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets, value = [stmt.target], stmt.value
        else:
            continue
        named = any(isinstance(t, ast.Name) and t.id == "ARTIFACT_TYPE" for t in targets)
        if named and isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value
    return None


# Versions


class CurrentIds:
    """Current version id per (dag, node), looked up once each."""

    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self._ids: dict[tuple[str, str], int | None] = {}

    def get(self, dag: str, node: str) -> int | None:
        key = (dag, node)
        if key not in self._ids:
            v = self.catalog.current_version(dag, node)
            self._ids[key] = v.id if v else None
        return self._ids[key]

    def is_current(self, v: VersionRow) -> bool:
        return self.get(v.dag, v.node) == v.id


def version_summary(v: VersionRow, current: CurrentIds) -> VersionSummary:
    stats = v.stats
    return VersionSummary(
        id=v.id,
        label=v.label,
        dag=v.dag,
        node=v.node,
        seq=v.seq,
        status=v.status,
        scratch=v.scratch,
        current=current.is_current(v),
        commit=v.commit,
        dirty=v.dirty,
        node_fp=v.node_fp,
        snapshot_id=v.snapshot_id,
        run_id=v.run_id,
        artifact_type=v.artifact_type,
        created_at=v.created_at,
        finalized_at=v.finalized_at,
        docs=int(stats.get("docs") or 0),
        ok=int(stats.get("ok") or 0),
        failed=int(stats.get("error") or 0),
        skipped=int(stats.get("skipped") or 0),
    )


def issue_counts(issues: Iterable[IssueRow]) -> IssueCounts:
    counts = IssueCounts()
    for i in issues:
        if i.level == "error":
            counts.error += 1
        elif i.level == "warning":
            counts.warning += 1
    return counts


def checks_fp(config: Config, checks: str | None, params: Mapping[str, Any]) -> str | None:
    """Fingerprint of a working-tree checks file; None without one (or when it is missing)."""
    if checks is None:
        return None
    path = config.repo_root / checks
    if not path.is_file():
        return None
    return fingerprint.checks_fingerprint(fingerprint.sha256_file(path), params)


def version_params(v: VersionRow, node: spec.NodeSpec | None) -> Mapping[str, Any]:
    params = v.meta.get("params")
    if isinstance(params, Mapping):
        return params
    return node.params if node is not None else {}


def checks_changed(config: Config, v: VersionRow, issues: Sequence[IssueRow]) -> bool:
    """The working-tree checks file (or its absence) differs from the one the version's script
    issues were computed with: the latest script issue's checks_fp, else meta["checks_fp"]."""
    found = find_dag(config, v.dag)
    node = found.dag.nodes.get(v.node) if found and found.dag else None
    if node is None:
        return False
    recorded = next((i.checks_fp for i in reversed(issues) if i.source == "script" and i.checks_fp), None)
    recorded = recorded or v.meta.get("checks_fp")
    return checks_fp(config, node.checks, version_params(v, node)) != recorded


def version_detail(config: Config, catalog: Catalog, objects: ObjectStore, v: VersionRow) -> VersionDetail:
    current = CurrentIds(catalog)
    inputs = []
    for node, vid in v.input_versions.items():
        iv = catalog.get_version(int(vid))
        inputs.append(InputVersion(node=node, version=version_summary(iv, current), current=current.is_current(iv)))
    issues = catalog.issues(v.id)
    ok, reasons = state.promotable(catalog, v.id)
    current_id = current.get(v.dag, v.node)
    replaces = None
    if current_id is not None and current_id != v.id:
        replaces = version_summary(catalog.get_version(current_id), current)
    return VersionDetail(
        **version_summary(v, current).model_dump(),
        inputs=inputs,
        meta=dict(v.meta),
        stats=dict(v.stats),
        summary=metrics.version_summary(catalog, objects, v.id),
        issues=issue_counts(issues),
        promotable=ok,
        not_promotable_reasons=list(reasons),
        replaces=replaces,
        noncurrent_inputs=[i.node for i in inputs if not i.current],
        checks_changed=checks_changed(config, v, issues),
    )


# Records


def vector_columns(schema: pa.Schema) -> list[str]:
    import pyarrow as pa

    return [
        f.name
        for f in schema
        if pa.types.is_fixed_size_list(f.type) and pa.types.is_floating(f.type.value_type)
    ]


def table_rows(table: pa.Table) -> tuple[list[dict[str, Any]], list[str]]:
    """Rows without vector columns, and the omitted column names."""
    omitted = vector_columns(table.schema)
    return table.drop_columns(omitted).to_pylist(), omitted


# Snapshots


def snapshot_out(s: SnapshotRow) -> SnapshotOut:
    return SnapshotOut(id=s.id, source=s.source, created_at=s.created_at, doc_count=s.doc_count, file_count=s.file_count)


def doc_fields(row: SnapshotDocRow | None) -> dict[str, Any]:
    """filename, path (primary), job_number, project from a snapshot row."""
    if row is None:
        return {"filename": None, "path": None, "job_number": None, "project": None}
    meta = sources.doc_meta_columns(row)
    return {
        "filename": meta["filename"],
        "path": row.paths[0] if row.paths else None,
        "job_number": meta["job_number"],
        "project": meta["project"],
    }


def matches(row: SnapshotDocRow | None, q: str | None) -> bool:
    """Case-insensitive substring of the filename, a path or the job number."""
    if not q:
        return True
    if row is None:
        return False
    needle = q.lower()
    values = [*row.paths, row.meta.get("filename"), row.meta.get("job_number")]
    return any(needle in str(v).lower() for v in values if v)


class DocLookup:
    """Snapshot rows of docs across a few snapshots, first match wins."""

    def __init__(self, catalog: Catalog, snapshot_ids: Iterable[int]) -> None:
        self.catalog = catalog
        self.snapshot_ids = list(dict.fromkeys(snapshot_ids))
        self._rows: dict[str, SnapshotDocRow | None] = {}

    def get(self, doc_id: str) -> SnapshotDocRow | None:
        if doc_id not in self._rows:
            self._rows[doc_id] = next(
                (r for sid in self.snapshot_ids if (r := self.catalog.snapshot_doc(sid, doc_id)) is not None), None
            )
        return self._rows[doc_id]

    def filename(self, doc_id: str) -> str | None:
        return doc_fields(self.get(doc_id))["filename"]


# Runs


def _duration(run: RunRow) -> float | None:
    if run.started_at is None:
        return None
    start = datetime.fromisoformat(run.started_at)
    end = datetime.fromisoformat(run.ended_at) if run.ended_at else datetime.now(UTC)
    return max((end - start).total_seconds(), 0.0)


def _counts(catalog: Catalog, run: RunRow) -> tuple[int, int, int]:
    """(processed, cached, failed): from the stats of the run's versions once they are all
    complete, else by counting the run's catalog events."""
    versions = catalog.run_versions(run.id)
    if run.status in TERMINAL and versions and all(v.status == "complete" for v in versions):
        return (
            sum(int(v.stats.get("processed") or 0) for v in versions),
            sum(int(v.stats.get("cached") or 0) for v in versions),
            sum(int(v.stats.get("error") or 0) for v in versions),
        )
    processed = cached = failed = 0
    for e in catalog.events(run.id):
        if e.kind == "doc_finished":
            processed += 1
        elif e.kind == "doc_failed":
            processed += 1
            failed += 1
        elif e.kind == "doc_cached":
            cached += 1
    return processed, cached, failed


def _doc_filename(catalog: Catalog, run: RunRow) -> str | None:
    """Single-doc runs: the request's filename or path, else the first event naming one."""
    payloads: list[Mapping[str, Any]] = [run.request, *(e.payload for e in catalog.events(run.id))]
    for p in payloads:
        if p.get("filename"):
            return str(p["filename"])
        if p.get("path"):
            return PurePosixPath(str(p["path"])).name
    return None


def run_summary(catalog: Catalog, run: RunRow) -> RunSummary:
    processed, cached, failed = _counts(catalog, run)
    nodes = run.request.get("nodes") or ([run.request["target"]] if run.request.get("target") else [])
    return RunSummary(
        id=run.id,
        dag=run.dag,
        kind=run.kind if run.kind in ("dag", "single_doc") else "dag",
        status=run.status,
        scratch=run.scratch,
        commit=run.commit,
        dirty=run.dirty,
        created_at=run.created_at,
        started_at=run.started_at,
        ended_at=run.ended_at,
        duration_s=_duration(run),
        nodes=list(nodes),
        processed=processed,
        cached=cached,
        failed=failed,
        error=run.error,
        cancel_requested=run.cancel_requested,
        doc_filename=_doc_filename(catalog, run) if run.kind == "single_doc" else None,
    )
