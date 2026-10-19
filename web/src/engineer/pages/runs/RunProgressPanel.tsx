import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useReducer, useRef } from "react";

import { queryKeys } from "@/api/hooks";
import { useEventSource } from "@/api/sse";
import type { RunEventOut } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

import { applyEvent, initialProgress, RUN_EVENTS, type NodeStatus, type RunProgress } from "./progress";

const REFRESH_ON = new Set(["run_started", "node_finished", "run_finished"]);

function isRunEvent(data: unknown): data is RunEventOut {
  return !!data && typeof data === "object" && typeof (data as RunEventOut).id === "number";
}

/** Per-node progress from the run's event stream. The server replays the history first, so the
 * state is rebuilt on every (re)connect; replayed events already applied are ignored by id. */
export function useRunProgress(runId: number, nodes: string[], active: boolean): RunProgress {
  const [state, dispatch] = useReducer(applyEvent, nodes, initialProgress);
  const client = useQueryClient();
  const activeRef = useRef(active);
  useEffect(() => {
    activeRef.current = active;
  });
  useEventSource(
    `/api/runs/${runId}/events`,
    (_name, data) => {
      if (!isRunEvent(data)) return;
      dispatch(data);
      // Live changes to status, versions, logs and metrics.
      if (activeRef.current && REFRESH_ON.has(data.kind)) {
        void client.invalidateQueries({ queryKey: queryKeys.run(runId) });
        void client.invalidateQueries({ queryKey: ["runs", "list"] });
      }
    },
    { events: RUN_EVENTS, closeOn: ["run_finished"] },
  );
  return state;
}

const STATUS: Record<NodeStatus, { label: string; className: string }> = {
  pending: { label: "Pending", className: "border-border bg-muted text-muted-foreground" },
  running: { label: "Running", className: "border-violet-200 bg-violet-50 text-violet-700" },
  finished: { label: "Finished", className: "border-emerald-200 bg-emerald-50 text-emerald-700" },
  skipped: { label: "Skipped", className: "border-border bg-muted text-muted-foreground" },
  failed: { label: "Failed", className: "border-red-200 bg-red-50 text-red-700" },
  cancelled: { label: "Cancelled", className: "border-amber-200 bg-amber-50 text-amber-700" },
};

export function RunProgressPanel({ progress }: { progress: RunProgress }) {
  if (progress.nodes.length === 0) return <p className="text-sm text-muted-foreground">No nodes</p>;
  return (
    <div className="grid grid-cols-[minmax(6rem,auto)_auto_minmax(8rem,1fr)_auto_auto] items-center gap-x-4 gap-y-2 text-sm">
      {progress.nodes.map((n) => {
        const { label, className } = STATUS[n.status];
        const percent = progress.total ? Math.min(100, (n.done / progress.total) * 100) : 0;
        return (
          <div key={n.node} className="contents" data-testid={`progress-${n.node}`}>
            <span className="font-mono">{n.node}</span>
            <Badge variant="outline" className={className}>
              {label}
            </Badge>
            <Progress value={percent} aria-label={`${n.node} progress`} />
            <span className="text-right tabular-nums">
              {n.done} / {progress.total ?? "—"}
            </span>
            <span className="text-xs text-muted-foreground tabular-nums">
              {n.processed} processed · {n.cached} cached ·{" "}
              <span className={cn(n.failed > 0 && "text-destructive")}>{n.failed} failed</span>
            </span>
          </div>
        );
      })}
    </div>
  );
}
