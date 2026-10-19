"""Version detail, docs, issues, revalidate, promote, debug files."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from ingest import obs, scripts, state, validation
from ingest.api.deps import CatalogDep, ConfigDep, ObjectsDep, bad_request, not_found, version_or_404
from ingest.api.models import (
    DebugFile,
    IssueCounts,
    IssueOut,
    PromoteResult,
    RevalidateResult,
    VersionDetail,
    VersionDocDetail,
    VersionDocOut,
    VersionDocsPage,
)
from ingest.api.views import (
    CurrentIds,
    DocLookup,
    checks_fp,
    doc_fields,
    find_dag,
    issue_counts,
    matches,
    table_rows,
    version_detail,
    version_params,
    version_summary,
)
from ingest.config import Config
from ingest.store.catalog import IssueRow, SnapshotDocRow, VersionDocRow, VersionRow
from ingest.store.objects import ObjectStore

router = APIRouter(tags=["versions"])


def _doc_out(d: VersionDocRow, row: SnapshotDocRow | None, issues: IssueCounts) -> dict[str, Any]:
    return dict(
        doc_id=d.doc_id,
        status=d.status,
        cached=d.cached,
        **doc_fields(row),
        error_type=d.error_type,
        error_message=d.error_message,
        duration_s=d.duration_s,
        peak_rss_mb=d.peak_rss_mb,
        metrics=dict(d.metrics),
        issues=issues,
    )


def _issue_out(i: IssueRow, lookup: DocLookup) -> IssueOut:
    return IssueOut(
        id=i.id,
        doc_id=i.doc_id,
        filename=lookup.filename(i.doc_id) if i.doc_id else None,
        source=i.source,
        level=i.level,
        code=i.code,
        message=i.message,
        page=i.page,
    )


def _debug_paths(config: Config, v: VersionRow, doc_id: str) -> list[Path]:
    if v.run_id is None:
        return []
    return obs.list_debug_files(config.run_dir(v.run_id), v.node, doc_id)


def _record(objects: ObjectStore, d: VersionDocRow) -> tuple[Any, list[str]]:
    if d.status != "ok" or d.object_sha is None or d.kind is None:
        return None, []
    if d.kind == "parquet":
        return table_rows(objects.get_table(d.object_sha))
    return objects.get_json(d.object_sha), []


@router.get("/versions/{version_id}")
def get_version(version_id: int, config: ConfigDep, catalog: CatalogDep, objects: ObjectsDep) -> VersionDetail:
    return version_detail(config, catalog, objects, version_or_404(catalog, version_id))


@router.get("/versions/{version_id}/docs")
def version_docs(
    version_id: int,
    catalog: CatalogDep,
    status: Literal["ok", "error", "skipped"] | None = None,
    q: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> VersionDocsPage:
    """Docs in snapshot (primary path) order; `q` matches filename, path or job number
    (case-insensitive substring)."""
    v = version_or_404(catalog, version_id)
    docs = {d.doc_id: d for d in catalog.version_docs(version_id)}
    rows = {r.doc_id: r for r in catalog.snapshot_docs(v.snapshot_id)}
    order = [doc_id for doc_id in rows if doc_id in docs] + [doc_id for doc_id in docs if doc_id not in rows]
    selected = [
        doc_id
        for doc_id in order
        if (status is None or docs[doc_id].status == status) and matches(rows.get(doc_id), q)
    ]
    by_doc: dict[str | None, list[IssueRow]] = {}
    for i in catalog.issues(version_id):
        by_doc.setdefault(i.doc_id, []).append(i)
    page = selected[max(offset, 0) : max(offset, 0) + max(limit, 0)]
    return VersionDocsPage(
        total=len(selected),
        items=[
            VersionDocOut(**_doc_out(docs[d], rows.get(d), issue_counts(by_doc.get(d, [])))) for d in page
        ],
    )


@router.get("/versions/{version_id}/docs/{doc_id}")
def version_doc(
    version_id: int, doc_id: str, config: ConfigDep, catalog: CatalogDep, objects: ObjectsDep
) -> VersionDocDetail:
    v = version_or_404(catalog, version_id)
    d = catalog.version_doc(version_id, doc_id)
    if d is None:
        raise not_found(f"Doc {doc_id!r} not in {v.label}")
    row = catalog.snapshot_doc(v.snapshot_id, doc_id)
    issues = catalog.issues(version_id, doc_id)
    lookup = DocLookup(catalog, [v.snapshot_id])
    record, omitted = _record(objects, d)
    return VersionDocDetail(
        **_doc_out(d, row, issue_counts(issues)),
        version=version_summary(v, CurrentIds(catalog)),
        paths=list(row.paths) if row else [],
        traceback=d.traceback,
        worker_pid=d.worker_pid,
        kind=d.kind if d.kind in ("json", "parquet") else None,
        record=record,
        omitted_columns=omitted,
        issue_list=[_issue_out(i, lookup) for i in issues],
        debug_files=[DebugFile(name=p.name, size=p.stat().st_size) for p in _debug_paths(config, v, doc_id)],
    )


@router.get("/versions/{version_id}/issues")
def version_issues(
    version_id: int,
    catalog: CatalogDep,
    level: Literal["error", "warning"] | None = None,
    source: Literal["hard", "quality", "script"] | None = None,
    doc: str | None = None,
) -> list[IssueOut]:
    v = version_or_404(catalog, version_id)
    lookup = DocLookup(catalog, [v.snapshot_id])
    return [
        _issue_out(i, lookup)
        for i in catalog.issues(version_id, doc)
        if (level is None or i.level == level) and (source is None or i.source == source)
    ]


@router.post("/versions/{version_id}/revalidate")
def revalidate(version_id: int, config: ConfigDep, catalog: CatalogDep, objects: ObjectsDep) -> RevalidateResult:
    """Re-run the node's working-tree checks file over the version (none clears script issues)."""
    v = version_or_404(catalog, version_id)
    found = find_dag(config, v.dag)
    node = found.dag.nodes.get(v.node) if found and found.dag else None
    if node is None:
        raise not_found(f"Node {v.dag}/{v.node} not found in the working tree")
    params = version_params(v, node)
    checks = None
    if node.checks is not None:
        try:
            checks = scripts.load_checks(config.repo_root / node.checks)
        except (scripts.ScriptError, OSError) as e:
            raise bad_request(str(e)) from None
    fp = checks_fp(config, node.checks, params) if checks else None
    return RevalidateResult(issues=validation.revalidate(catalog, objects, version_id, checks, params, fp))


@router.post("/versions/{version_id}/promote")
def promote(version_id: int, config: ConfigDep, catalog: CatalogDep) -> PromoteResult:
    """409 with the reasons when the version isn't promotable."""
    v = version_or_404(catalog, version_id)
    before = catalog.current_version(v.dag, v.node)
    try:
        warnings = state.promote(catalog, config, version_id)
    except ValueError as e:
        _, reasons = state.promotable(catalog, version_id)
        raise HTTPException(status.HTTP_409_CONFLICT, "; ".join(reasons) or str(e)) from None
    current = CurrentIds(catalog)
    return PromoteResult(
        version=version_summary(catalog.get_version(version_id), current),
        replaced=version_summary(before, current) if before is not None and before.id != version_id else None,
        warnings=list(warnings),
    )


@router.get("/versions/{version_id}/docs/{doc_id}/debug")
def debug_files(version_id: int, doc_id: str, config: ConfigDep, catalog: CatalogDep) -> list[DebugFile]:
    v = version_or_404(catalog, version_id)
    return [DebugFile(name=p.name, size=p.stat().st_size) for p in _debug_paths(config, v, doc_id)]


@router.get("/versions/{version_id}/docs/{doc_id}/debug/{name}", response_class=FileResponse)
def debug_file(version_id: int, doc_id: str, name: str, config: ConfigDep, catalog: CatalogDep) -> FileResponse:
    v = version_or_404(catalog, version_id)
    path = next((p for p in _debug_paths(config, v, doc_id) if p.name == name), None)
    if path is None:
        raise not_found(f"Debug file {name!r} not found")
    return FileResponse(path)

