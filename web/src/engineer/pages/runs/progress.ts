import type { RunEventOut, RunStatus } from "@/api/types";

/** Catalog run event kinds streamed on /api/runs/{id}/events. */
export const RUN_EVENTS = [
  "run_queued",
  "run_started",
  "run_waiting",
  "node_started",
  "node_skipped",
  "doc_cached",
  "doc_skipped",
  "doc_finished",
  "doc_failed",
  "node_finished",
  "run_finished",
];

export type NodeStatus = "pending" | "running" | "finished" | "skipped" | "failed" | "cancelled";

export interface NodeProgress {
  node: string;
  status: NodeStatus;
  done: number;
  processed: number;
  cached: number;
  failed: number;
  skipped: number;
}

export interface RunProgress {
  /** Docs per node, from run_started. */
  total: number | null;
  nodes: NodeProgress[];
  /** Id of the last applied event; events at or below it are replays and ignored. */
  lastId: number;
  status: RunStatus | null;
  /** From run_started. */
  snapshotId: number | null;
}

export function initialProgress(nodes: string[]): RunProgress {
  return { total: null, nodes: nodes.map(emptyNode), lastId: 0, status: null, snapshotId: null };
}

function emptyNode(node: string): NodeProgress {
  return { node, status: "pending", done: 0, processed: 0, cached: 0, failed: 0, skipped: 0 };
}

function num(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

/** Applies one run event. Idempotent per event id, so a replayed history rebuilds the same state. */
export function applyEvent(state: RunProgress, event: RunEventOut): RunProgress {
  if (event.id <= state.lastId) return state;
  const p = event.payload ?? {};
  const next: RunProgress = { ...state, lastId: event.id };

  const updateNode = (name: unknown, update: (n: NodeProgress) => NodeProgress): RunProgress => {
    if (typeof name !== "string") return next;
    const index = next.nodes.findIndex((n) => n.node === name);
    const nodes = [...next.nodes];
    if (index === -1) nodes.push(update(emptyNode(name)));
    else nodes[index] = update(nodes[index]);
    return { ...next, nodes };
  };

  switch (event.kind) {
    case "run_queued":
    case "run_waiting":
      return { ...next, status: "queued" };
    case "run_started": {
      const docs = Array.isArray(p.docs) ? p.docs.length : num(p.docs);
      let nodes = next.nodes;
      if (Array.isArray(p.nodes)) {
        const names = p.nodes.filter((n): n is string => typeof n === "string");
        nodes = [
          ...names.map((name) => next.nodes.find((n) => n.node === name) ?? emptyNode(name)),
          ...next.nodes.filter((n) => !names.includes(n.node)),
        ];
      }
      return {
        ...next,
        status: "running",
        total: docs ?? next.total,
        nodes,
        snapshotId: num(p.snapshot_id) ?? next.snapshotId,
      };
    }
    case "node_started":
      return updateNode(p.node, (n) => ({ ...n, status: "running" }));
    case "node_skipped":
      return updateNode(p.node, (n) => ({ ...n, status: "skipped" }));
    case "doc_cached":
      return updateNode(p.node, (n) => ({ ...n, done: n.done + 1, cached: n.cached + 1 }));
    case "doc_skipped":
      return updateNode(p.node, (n) => ({ ...n, done: n.done + 1, skipped: n.skipped + 1 }));
    case "doc_finished":
      return updateNode(p.node, (n) => ({ ...n, done: n.done + 1, processed: n.processed + 1 }));
    case "doc_failed":
      return updateNode(p.node, (n) => ({
        ...n,
        done: n.done + 1,
        processed: n.processed + 1,
        failed: n.failed + 1,
      }));
    case "node_finished":
      return updateNode(p.node, (n) => ({
        ...n,
        status: "finished",
        done: Math.max(n.done, next.total ?? 0),
        processed: num(p.processed) ?? n.processed,
        cached: num(p.cached) ?? n.cached,
        failed: num(p.error) ?? n.failed,
        skipped: num(p.skipped) ?? n.skipped,
      }));
    case "run_finished": {
      const status = typeof p.status === "string" ? (p.status as RunStatus) : "succeeded";
      const stopped: NodeStatus | null = status === "failed" ? "failed" : status === "cancelled" ? "cancelled" : null;
      return {
        ...next,
        status,
        nodes: stopped ? next.nodes.map((n) => (n.status === "running" ? { ...n, status: stopped } : n)) : next.nodes,
      };
    }
    default:
      return next;
  }
}
