import { describe, expect, it } from "vitest";

import type { RunEventOut } from "@/api/types";

import { applyEvent, initialProgress, type RunProgress } from "./progress";

let nextId = 0;
function ev(kind: string, payload: Record<string, unknown> = {}): RunEventOut {
  nextId += 1;
  return { id: nextId, ts: "2026-10-19T12:00:00Z", kind, payload };
}

function replay(events: RunEventOut[], state: RunProgress = initialProgress(["parse", "chunk"])): RunProgress {
  return events.reduce(applyEvent, state);
}

function history(): RunEventOut[] {
  nextId = 0;
  return [
    ev("run_queued"),
    ev("run_started", { docs: 3, nodes: ["parse", "chunk"], snapshot_id: 4 }),
    ev("node_started", { node: "parse", version_id: 10 }),
    ev("doc_cached", { node: "parse", doc_id: "a" }),
    ev("doc_finished", { node: "parse", doc_id: "b" }),
    ev("doc_failed", { node: "parse", doc_id: "c", error_type: "ValueError" }),
    ev("node_finished", { node: "parse", version_id: 10, processed: 2, cached: 1, ok: 2, error: 1, skipped: 0 }),
    ev("node_started", { node: "chunk", version_id: 11 }),
    ev("doc_finished", { node: "chunk", doc_id: "a" }),
  ];
}

describe("applyEvent", () => {
  it("tracks per-node progress, status and counts", () => {
    const state = replay(history());
    expect(state.total).toBe(3);
    expect(state.snapshotId).toBe(4);
    expect(state.status).toBe("running");
    expect(state.nodes).toEqual([
      { node: "parse", status: "finished", done: 3, processed: 2, cached: 1, failed: 1, skipped: 0 },
      { node: "chunk", status: "running", done: 1, processed: 1, cached: 0, failed: 0, skipped: 0 },
    ]);
  });

  it("ignores replayed events, so a reconnect rebuilds the same state", () => {
    const events = history();
    const once = replay(events);
    // Reconnect: the server replays the whole history (or an overlapping tail).
    const again = replay([...events, ...events.slice(4)], once);
    expect(again).toEqual(once);
    expect(replay(events.slice(0, 5).concat(events))).toEqual(once);
  });

  it("counts skipped docs, skipped nodes and marks running nodes when the run stops", () => {
    nextId = 0;
    const state = replay([
      ev("run_started", { docs: 2 }),
      ev("node_started", { node: "parse" }),
      ev("doc_skipped", { node: "parse", doc_id: "a" }),
      ev("node_skipped", { node: "chunk" }),
      ev("run_finished", { status: "cancelled" }),
    ]);
    expect(state.status).toBe("cancelled");
    expect(state.nodes.map((n) => [n.node, n.status, n.done, n.skipped])).toEqual([
      ["parse", "cancelled", 1, 1],
      ["chunk", "skipped", 0, 0],
    ]);
  });

  it("adds nodes it did not know about", () => {
    nextId = 0;
    const state = replay([ev("node_started", { node: "embed" })], initialProgress([]));
    expect(state.nodes.map((n) => n.node)).toEqual(["embed"]);
  });
});
