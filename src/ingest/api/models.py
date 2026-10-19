"""Request and response models of the engineer API (Ingestion-Design §16). The web client's types
are generated from the OpenAPI schema these produce (web: `npm run gen:api`)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

StateName = Literal["fresh", "stale", "candidate", "running", "failed", "never_run"]
RunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
RunKind = Literal["dag", "single_doc"]
VersionStatus = Literal["building", "complete", "abandoned"]
DocStatus = Literal["ok", "error", "skipped"]
Selection = Literal["node", "downstream", "upstream", "all"]


# Versions


class VersionSummary(BaseModel):
    id: int
    label: str
    """<dag>/<node>@vN"""
    dag: str
    node: str
    seq: int | None
    status: VersionStatus
    scratch: bool
    current: bool
    commit: str | None
    dirty: bool
    node_fp: str
    snapshot_id: int
    run_id: int | None
    artifact_type: str | None
    created_at: str
    finalized_at: str | None
    docs: int
    ok: int
    failed: int
    skipped: int


class InputVersion(BaseModel):
    node: str
    version: VersionSummary
    current: bool
    """Whether this input version is its node's current version now."""


class IssueCounts(BaseModel):
    error: int = 0
    warning: int = 0


class VersionDetail(VersionSummary):
    inputs: list[InputVersion]
    meta: dict[str, Any]
    """Lineage: script, script hash, params, env hash, describe() output, …"""
    stats: dict[str, Any]
    summary: dict[str, Any]
    """metrics.version_summary: generic counts plus per-type totals."""
    issues: IssueCounts
    promotable: bool
    not_promotable_reasons: list[str]
    replaces: VersionSummary | None
    """The node's current version, which promoting this one would replace."""
    noncurrent_inputs: list[str]
    """Input nodes whose version used here isn't current."""
    checks_changed: bool
    """The node's checks file changed since the version's script issues were computed."""


class VersionDocOut(BaseModel):
    doc_id: str
    status: DocStatus
    cached: bool
    filename: str | None
    path: str | None
    job_number: str | None
    project: str | None
    error_type: str | None
    error_message: str | None
    duration_s: float | None
    peak_rss_mb: float | None
    metrics: dict[str, float]
    issues: IssueCounts


class VersionDocsPage(BaseModel):
    total: int
    items: list[VersionDocOut]


class IssueOut(BaseModel):
    id: int
    doc_id: str | None
    filename: str | None
    source: Literal["hard", "quality", "script"]
    level: str
    code: str
    message: str
    page: int | None


class VersionDocDetail(VersionDocOut):
    version: VersionSummary
    paths: list[str]
    traceback: str | None
    worker_pid: int | None
    kind: Literal["json", "parquet"] | None
    record: Any
    """The doc's record: a JSON object, or a list of row objects for tables. None unless ok."""
    omitted_columns: list[str]
    """Table columns left out of `record` (vectors)."""
    issue_list: list[IssueOut]
    debug_files: list[DebugFile]


class DebugFile(BaseModel):
    name: str
    size: int


class RevalidateResult(BaseModel):
    issues: int


class PromoteResult(BaseModel):
    version: VersionSummary
    replaced: VersionSummary | None
    warnings: list[str]


# DAGs


class NodeInfo(BaseModel):
    name: str
    script: str
    params: dict[str, Any]
    inputs: list[str]
    checks: str | None
    timeout: float
    workers: int
    artifact_type: str | None
    state: StateName
    reasons: list[str]
    dirty: bool
    fingerprint: str
    current: VersionSummary | None
    candidates: list[VersionSummary]
    scratch: list[VersionSummary]
    doc_count: int | None
    """Docs in the current version."""
    failure_count: int | None
    """Failed docs in the current version."""
    active_run_id: int | None


class DagSummary(BaseModel):
    name: str
    source: str | None
    path: str
    dirty: bool
    """Uncommitted changes in the DAG file or any node's files."""
    errors: list[str]
    node_count: int
    states: dict[str, int]
    """Node state → number of nodes."""
    last_run: RunSummary | None


class DagDetail(BaseModel):
    name: str
    source: str | None
    path: str
    dirty: bool
    errors: list[str]
    nodes: list[NodeInfo]
    """Topological order."""


# Runs


class RunRequestIn(BaseModel):
    dag: str
    target: str | None = None
    selection: Selection = "all"
    snapshot_id: int | None = None
    input_versions: dict[str, int] = Field(default_factory=dict)
    recompute: Literal["reuse", "force"] = "reuse"
    docs: Literal["all"] | int | list[str] = "all"
    code: Literal["auto", "committed", "working"] = "auto"
    sample_seed: int = 0


class PlanInput(BaseModel):
    node: str
    version: VersionSummary | None
    """None when the input is produced earlier in the same run."""
    fingerprint: str


class NodePlanOut(BaseModel):
    node: str
    script: str
    fingerprint: str
    artifact_type: str | None
    inputs: list[PlanInput]
    docs_total: int
    docs_cached: int
    docs_to_process: int
    docs_blocked: int


class RunPlanOut(BaseModel):
    code: Literal["committed", "working"]
    commit: str | None
    dirty: bool
    snapshot_id: int
    doc_count: int
    scratch: bool
    scratch_reasons: list[str]
    nodes: list[NodePlanOut]


class RunCreated(BaseModel):
    run_id: int


class RunSummary(BaseModel):
    id: int
    dag: str
    kind: RunKind
    status: RunStatus
    scratch: bool
    commit: str | None
    dirty: bool
    created_at: str
    started_at: str | None
    ended_at: str | None
    duration_s: float | None
    nodes: list[str]
    processed: int
    cached: int
    failed: int
    error: str | None
    cancel_requested: bool
    doc_filename: str | None
    """Single-doc runs: the document's filename."""


class RunDetail(RunSummary):
    request: dict[str, Any]
    versions: list[VersionDetail]
    """Versions the run produced, in node order."""


class RunEventOut(BaseModel):
    """One catalog run event; also the `data` of each SSE event on /runs/{id}/events (SSE event
    name = kind, id = event id)."""

    id: int
    ts: str
    kind: str
    payload: dict[str, Any]


class LogLine(BaseModel):
    model_config = ConfigDict(extra="allow")

    ts: str
    level: str
    kind: str
    message: str
    run_id: int | None = None
    dag: str | None = None
    node: str | None = None
    version_id: int | None = None
    doc_id: str | None = None
    event: str | None = None


class NodeMetrics(BaseModel):
    node: str
    metrics: dict[str, float]
    """wall_s, docs_processed, docs_cached, docs_failed, cache_hit_rate, failure_rate,
    model_load_s, pages_per_s, … as recorded."""


class SlowDoc(BaseModel):
    node: str
    doc_id: str
    filename: str | None
    duration_s: float
    peak_rss_mb: float | None


class RunMetricsOut(BaseModel):
    run: dict[str, float]
    """Run-level metrics (rows without a node)."""
    nodes: list[NodeMetrics]
    slowest: list[SlowDoc]
    """Slowest docs per node, longest first."""


# Compare


class DocChangeOut(BaseModel):
    doc_id: str
    filename: str | None
    job_number: str | None
    project: str | None
    a_records: int | None
    b_records: int | None
    change: float


class ComparisonOut(BaseModel):
    a: VersionSummary
    b: VersionSummary
    a_summary: dict[str, Any]
    b_summary: dict[str, Any]
    changed: list[DocChangeOut]
    unchanged: int


class DocDiffOut(BaseModel):
    a: Any
    b: Any
    """Each side's record (tables as lists of row objects, vectors omitted), or None."""
    only_a: list[str] | None
    only_b: list[str] | None
    """Tables: text_hash values present on one side only."""


# Search


class SearchIn(BaseModel):
    query: str
    versions: list[int] = Field(min_length=1, max_length=2)
    k: int = Field(default=10, ge=1, le=100)


class Region(BaseModel):
    page: int
    x0: float
    y0: float
    x1: float
    y1: float


class SearchHit(BaseModel):
    rank: int
    score: float
    chunk_id: str
    doc_id: str
    text_hash: str | None
    filename: str | None
    job_number: str | None
    project: str | None
    pages: list[int]
    section: list[str]
    text: str
    regions: list[Region]


class SearchSide(BaseModel):
    version: VersionSummary
    model: str | None
    hits: list[SearchHit]


class SearchOut(BaseModel):
    sides: list[SearchSide]


# Sources


class SnapshotOut(BaseModel):
    id: int
    source: str
    created_at: str
    doc_count: int
    file_count: int


class SourceOut(BaseModel):
    name: str
    folders: list[str]
    glob: str
    snapshots: list[SnapshotOut]
    """Newest first."""


class DocRef(BaseModel):
    doc_id: str
    path: str
    filename: str | None
    job_number: str | None
    project: str | None


class SnapshotDiffOut(BaseModel):
    old: SnapshotOut | None
    new: SnapshotOut
    added: list[DocRef]
    removed: list[DocRef]
    changed_paths: list[str]
    unchanged: int


class SyncOut(BaseModel):
    snapshot: SnapshotOut
    created: bool
    diff: SnapshotDiffOut


class SnapshotDocOut(DocRef):
    paths: list[str]
    size: int
    flags: list[str]
    flag_reason: str | None


VersionDocDetail.model_rebuild()
DagSummary.model_rebuild()
