import type { RunPlanOut, RunRequestIn, VersionSummary } from "@/api/types";

export type Selection = RunRequestIn["selection"];
export type DocsMode = "all" | "sample" | "pick";
export type Code = "committed" | "working";

export interface LauncherState {
  selection: Selection;
  /** null = latest. */
  snapshotId: number | null;
  /** Input node → chosen version, for inputs read from outside the run. */
  inputVersions: Record<string, number>;
  recompute: "reuse" | "force";
  docsMode: DocsMode;
  sampleSize: number;
  picked: string[];
  /** null until the user chooses: the planner decides ("auto"). */
  code: Code | null;
}

export const DEFAULT_SAMPLE_SIZE = 10;

/** The launcher's fields as opened from a node (that node only) or a document (those docs). */
export function initialLauncherState(node?: string, docIds?: string[]): LauncherState {
  return {
    selection: node ? "node" : "all",
    snapshotId: null,
    inputVersions: {},
    recompute: "reuse",
    docsMode: docIds?.length ? "pick" : "all",
    sampleSize: DEFAULT_SAMPLE_SIZE,
    picked: docIds ?? [],
    code: null,
  };
}

/** The run request for the launcher's fields; null while they don't describe a run. */
export function buildRunRequest(dag: string, target: string | undefined, s: LauncherState): RunRequestIn | null {
  if (s.selection !== "all" && !target) return null;
  let docs: RunRequestIn["docs"];
  if (s.docsMode === "all") docs = "all";
  else if (s.docsMode === "sample") {
    if (!Number.isInteger(s.sampleSize) || s.sampleSize < 1) return null;
    docs = s.sampleSize;
  } else {
    if (s.picked.length === 0) return null;
    docs = s.picked;
  }
  return {
    dag,
    target: s.selection === "all" ? null : target,
    selection: s.selection,
    snapshot_id: s.snapshotId,
    input_versions: s.inputVersions,
    recompute: s.recompute,
    docs,
    code: s.code ?? "auto",
    sample_seed: 0,
  };
}

/** Inputs of the planned nodes that are read from existing versions (not produced in the run), once each. */
export function externalInputs(plan: RunPlanOut): { node: string; version: VersionSummary }[] {
  const seen = new Map<string, VersionSummary>();
  for (const n of plan.nodes) {
    for (const input of n.inputs) {
      if (input.version && !seen.has(input.node)) seen.set(input.node, input.version);
    }
  }
  return [...seen].map(([node, version]) => ({ node, version }));
}

/** The code choice to show: the user's, else working tree when the selected nodes have uncommitted changes. */
export function shownCode(code: Code | null, plan: RunPlanOut | undefined): Code | undefined {
  if (code) return code;
  if (!plan) return undefined;
  return plan.dirty ? "working" : "committed";
}
