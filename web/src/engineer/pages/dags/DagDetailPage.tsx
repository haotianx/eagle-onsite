import { useQuery } from "@tanstack/react-query";
import { getRouteApi } from "@tanstack/react-router";
import { useState } from "react";

import { api, unwrap } from "@/api/client";
import { queryKeys } from "@/api/hooks";
import type { DagDetail } from "@/api/types";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";

import { PromoteDialog } from "../versions/PromoteDialog";
import { DagGraph } from "./DagGraph";
import { NodePanel } from "./NodePanel";
import { RunLauncher } from "./RunLauncher";
import { UncommittedBadge } from "./UncommittedBadge";

const route = getRouteApi("/dags/$dag");

const RUNNING_REFETCH_MS = 3_000;

/** Whether any node of the DAG is being built, so its states should be polled. */
export function isRunning(dag: DagDetail | undefined): boolean {
  return !!dag?.nodes.some((n) => n.state === "running" || n.active_run_id != null);
}

export function DagDetailPage() {
  const { dag } = route.useParams();
  const { node } = route.useSearch();
  const navigate = route.useNavigate();
  const query = useQuery({
    queryKey: queryKeys.dag(dag),
    queryFn: () => unwrap(api.GET("/api/dags/{dag}", { params: { path: { dag } } })),
    refetchInterval: (q) => (isRunning(q.state.data) ? RUNNING_REFETCH_MS : false),
  });
  const [launcherOpen, setLauncherOpen] = useState(false);
  const [promoteId, setPromoteId] = useState<number | null>(null);

  const select = (name: string | undefined) => {
    void navigate({ search: (prev) => ({ ...prev, node: name }), replace: true });
  };

  if (query.isPending) {
    return (
      <div className="space-y-3 p-4">
        <Skeleton className="h-6 w-48" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }
  if (query.isError) {
    return (
      <div className="p-4">
        <Alert variant="destructive">
          <AlertDescription>{query.error.message}</AlertDescription>
        </Alert>
      </div>
    );
  }

  const detail = query.data;
  const selected = detail.nodes.find((n) => n.name === node);

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-11 shrink-0 items-center gap-3 border-b px-4">
        <h1 className="text-sm font-semibold">{detail.name}</h1>
        {detail.source && <span className="text-sm text-muted-foreground">{detail.source}</span>}
        {detail.dirty && <UncommittedBadge />}
      </div>
      {detail.errors.length > 0 && (
        <div className="border-b p-4">
          <Alert variant="destructive">
            <AlertDescription>
              <ul>
                {detail.errors.map((error) => (
                  <li key={error}>{error}</li>
                ))}
              </ul>
            </AlertDescription>
          </Alert>
        </div>
      )}
      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1">
          <DagGraph key={detail.name} nodes={detail.nodes} selected={selected?.name} onSelect={select} />
        </div>
        {selected && (
          <NodePanel
            node={selected}
            onRun={() => setLauncherOpen(true)}
            onPromote={setPromoteId}
            className="w-96 shrink-0 overflow-y-auto border-l"
          />
        )}
      </div>
      <RunLauncher dag={detail.name} node={selected?.name} open={launcherOpen} onOpenChange={setLauncherOpen} />
      {promoteId != null && (
        <PromoteDialog
          versionId={promoteId}
          open
          onOpenChange={(open) => {
            if (!open) setPromoteId(null);
          }}
        />
      )}
    </div>
  );
}
