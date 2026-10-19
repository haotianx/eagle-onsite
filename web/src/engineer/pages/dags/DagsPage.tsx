import { Link, useNavigate } from "@tanstack/react-router";

import { useDags } from "@/api/hooks";
import type { StateName } from "@/api/types";
import { NodeStateBadge } from "@/components/NodeStateBadge";
import { RunStatusBadge } from "@/components/RunStatusBadge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatRelative } from "@/lib/format";

import { UncommittedBadge } from "./UncommittedBadge";

const STATE_ORDER: StateName[] = ["running", "failed", "candidate", "stale", "fresh", "never_run"];

/** Non-zero node-state counts in a fixed order. */
export function stateCounts(states: Record<string, number>): [StateName, number][] {
  return STATE_ORDER.filter((s) => (states[s] ?? 0) > 0).map((s) => [s, states[s]]);
}

export function DagsPage() {
  const dags = useDags();
  const navigate = useNavigate();

  if (dags.isPending) {
    return (
      <div className="space-y-2 p-4">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
      </div>
    );
  }
  if (dags.isError) {
    return (
      <div className="p-4">
        <Alert variant="destructive">
          <AlertDescription>{dags.error.message}</AlertDescription>
        </Alert>
      </div>
    );
  }
  if (dags.data.length === 0) return <div className="p-4 text-sm text-muted-foreground">No DAGs</div>;

  return (
    <div className="p-4">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>DAG</TableHead>
            <TableHead>Source</TableHead>
            <TableHead>Last run</TableHead>
            <TableHead>Nodes</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {dags.data.map((dag) => (
            <TableRow
              key={dag.name}
              className="cursor-pointer align-top"
              onClick={() => void navigate({ to: "/dags/$dag", params: { dag: dag.name } })}
            >
              <TableCell>
                <div className="flex items-center gap-2">
                  <Link
                    to="/dags/$dag"
                    params={{ dag: dag.name }}
                    className="font-medium hover:underline"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {dag.name}
                  </Link>
                  {dag.dirty && <UncommittedBadge />}
                </div>
                {dag.errors.length > 0 && (
                  <ul className="mt-1 text-xs whitespace-normal text-destructive">
                    {dag.errors.map((error) => (
                      <li key={error}>{error}</li>
                    ))}
                  </ul>
                )}
              </TableCell>
              <TableCell>{dag.source ?? "—"}</TableCell>
              <TableCell>
                {dag.last_run ? (
                  <Link
                    to="/runs/$runId"
                    params={{ runId: String(dag.last_run.id) }}
                    className="inline-flex items-center gap-2 hover:underline"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <RunStatusBadge status={dag.last_run.status} />
                    <span className="text-muted-foreground">{formatRelative(dag.last_run.created_at)}</span>
                  </Link>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </TableCell>
              <TableCell>
                <div className="flex flex-wrap gap-1">
                  {stateCounts(dag.states).map(([state, count]) => (
                    <span key={state} className="inline-flex items-center gap-1">
                      <NodeStateBadge state={state} />
                      <span className="text-xs tabular-nums">{count}</span>
                    </span>
                  ))}
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
