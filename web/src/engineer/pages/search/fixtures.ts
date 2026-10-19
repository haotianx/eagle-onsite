import type { NodeInfo, SearchHit, VersionSummary } from "@/api/types";

export function version(id: number, extra: Partial<VersionSummary> = {}): VersionSummary {
  return {
    id,
    label: `default/embed@v${id}`,
    dag: "default",
    node: "embed",
    seq: id,
    status: "complete",
    scratch: false,
    current: false,
    commit: "abcdef0123",
    dirty: false,
    node_fp: "f".repeat(64),
    snapshot_id: 1,
    run_id: id,
    artifact_type: "embed",
    created_at: `2026-10-${String(10 + id).padStart(2, "0")}T12:00:00Z`,
    finalized_at: null,
    docs: 6,
    ok: 6,
    failed: 0,
    skipped: 0,
    ...extra,
  };
}

export function node(name: string, extra: Partial<NodeInfo> = {}): NodeInfo {
  return {
    name,
    script: `nodes/${name}.py`,
    params: {},
    inputs: [],
    checks: null,
    timeout: 60,
    workers: 1,
    artifact_type: name === "embed" ? "embed" : name,
    state: "fresh",
    reasons: [],
    dirty: false,
    fingerprint: "a".repeat(64),
    current: null,
    candidates: [],
    scratch: [],
    doc_count: null,
    failure_count: null,
    active_run_id: null,
    ...extra,
  };
}

export function hit(rank: number, extra: Partial<SearchHit> = {}): SearchHit {
  return {
    rank,
    score: 0.9 - rank / 100,
    chunk_id: `c${rank}`,
    doc_id: "d1",
    text_hash: `h${rank}`,
    filename: "proposal.pdf",
    job_number: "2292",
    project: "SZEKERES",
    pages: [3, 4],
    section: ["Scope", "Electrical"],
    text: `passage ${rank}`,
    regions: [],
    ...extra,
  };
}
