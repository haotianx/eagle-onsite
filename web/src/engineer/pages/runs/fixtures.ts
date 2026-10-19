import type { RunDetail, RunSummary, VersionDetail } from "@/api/types";

export function runSummary(extra: Partial<RunSummary> = {}): RunSummary {
  return {
    id: 7,
    dag: "default",
    kind: "dag",
    status: "succeeded",
    scratch: false,
    commit: "abcdef0123456",
    dirty: false,
    created_at: "2026-10-19T12:00:00Z",
    started_at: "2026-10-19T12:00:01Z",
    ended_at: "2026-10-19T12:01:00Z",
    duration_s: 59,
    nodes: ["parse", "chunk"],
    processed: 5,
    cached: 1,
    failed: 1,
    error: null,
    cancel_requested: false,
    doc_filename: null,
    ...extra,
  };
}

export function versionDetail(extra: Partial<VersionDetail> = {}): VersionDetail {
  return {
    id: 10,
    label: "default/parse@v2",
    dag: "default",
    node: "parse",
    seq: 2,
    status: "complete",
    scratch: false,
    current: false,
    commit: "abcdef0123456",
    dirty: false,
    node_fp: "f".repeat(64),
    snapshot_id: 3,
    run_id: 7,
    artifact_type: "parse",
    created_at: "2026-10-19T12:00:00Z",
    finalized_at: "2026-10-19T12:01:00Z",
    docs: 6,
    ok: 5,
    failed: 1,
    skipped: 0,
    inputs: [],
    meta: {},
    stats: {},
    summary: {},
    issues: { error: 0, warning: 0 },
    promotable: true,
    not_promotable_reasons: [],
    replaces: null,
    noncurrent_inputs: [],
    checks_changed: false,
    ...extra,
  };
}

export function runDetail(extra: Partial<RunDetail> = {}): RunDetail {
  return { ...runSummary(), request: {}, versions: [], ...extra };
}
