import type { DagDetail, NodeInfo, NodePlanOut, RunPlanOut, VersionSummary } from "@/api/types";

export function version(id: number, node: string, seq: number | null, extra: Partial<VersionSummary> = {}): VersionSummary {
  return {
    id,
    label: seq == null ? `default/${node}@scratch-${id}` : `default/${node}@v${seq}`,
    dag: "default",
    node,
    seq,
    status: "complete",
    scratch: seq == null,
    current: false,
    commit: "abcdef0123",
    dirty: false,
    node_fp: "f".repeat(64),
    snapshot_id: 1,
    run_id: 1,
    artifact_type: node,
    created_at: "2026-10-19T12:00:00Z",
    finalized_at: "2026-10-19T12:01:00Z",
    docs: 6,
    ok: 6,
    failed: 0,
    skipped: 0,
    ...extra,
  };
}

export function nodeInfo(name: string, inputs: string[], extra: Partial<NodeInfo> = {}): NodeInfo {
  return {
    name,
    script: `nodes/${name}.py`,
    params: {},
    inputs,
    checks: null,
    timeout: 600,
    workers: 1,
    artifact_type: name,
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

export const parse = nodeInfo("parse", [], {
  params: { ocr: true, engine: "docling" },
  checks: "checks/parse.py",
  current: version(1, "parse", 1, { current: true }),
  doc_count: 6,
  failure_count: 1,
});

export const chunk = nodeInfo("chunk", ["parse"], {
  state: "candidate",
  reasons: ["candidate available"],
  current: version(2, "chunk", 1, { current: true }),
  candidates: [version(3, "chunk", 2), version(5, "chunk", 3)],
  scratch: [version(4, "chunk", null)],
  doc_count: 6,
  failure_count: 0,
});

export const embed = nodeInfo("embed", ["chunk"], { state: "never_run" });

export const dagDetail: DagDetail = {
  name: "default",
  source: "proposals",
  path: "dags/default.yaml",
  dirty: true,
  errors: [],
  nodes: [parse, chunk, embed],
};

export function nodePlan(node: string, extra: Partial<NodePlanOut> = {}): NodePlanOut {
  return {
    node,
    script: `nodes/${node}.py`,
    fingerprint: "b".repeat(64),
    artifact_type: node,
    inputs: [],
    docs_total: 6,
    docs_cached: 2,
    docs_to_process: 4,
    docs_blocked: 0,
    ...extra,
  };
}

export function runPlan(extra: Partial<RunPlanOut> = {}): RunPlanOut {
  return {
    code: "committed",
    commit: "abcdef0123",
    dirty: false,
    snapshot_id: 7,
    doc_count: 6,
    scratch: false,
    scratch_reasons: [],
    nodes: [],
    ...extra,
  };
}
