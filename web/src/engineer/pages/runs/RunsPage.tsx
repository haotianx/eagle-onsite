import { getRouteApi, Link } from "@tanstack/react-router";

import { queryKeys, useDags, useRuns } from "@/api/hooks";
import type { RunSummary } from "@/api/types";
import { RunKindBadge } from "@/components/RunKindBadge";
import { RunStatusBadge } from "@/components/RunStatusBadge";
import { ScratchBadge } from "@/components/ScratchBadge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime, formatDuration, formatRelative } from "@/lib/format";

import { CancelRunButton, isActive, usePolling } from "./common";

const route = getRouteApi("/runs");

const ALL = "__all__";

export function RunsPage() {
  const { dag } = route.useSearch();
  const navigate = route.useNavigate();
  const dags = useDags();
  const runs = useRuns(dag);
  usePolling(runs.data?.some((r) => isActive(r.status)) ?? false, queryKeys.runs(dag));

  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold">Runs</h1>
        <Select
          value={dag ?? ALL}
          onValueChange={(value) => void navigate({ search: { dag: value === ALL ? undefined : value } })}
        >
          <SelectTrigger size="sm" aria-label="DAG" className="min-w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All DAGs</SelectItem>
            {dag && !dags.data?.some((d) => d.name === dag) && <SelectItem value={dag}>{dag}</SelectItem>}
            {dags.data?.map((d) => (
              <SelectItem key={d.name} value={d.name}>
                {d.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {runs.isPending ? (
        <Skeleton className="h-40 w-full" />
      ) : runs.isError ? (
        <p className="text-sm text-destructive">{runs.error.message}</p>
      ) : runs.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">No runs</p>
      ) : (
        <RunsTable runs={runs.data} />
      )}
    </div>
  );
}

export function RunsTable({ runs }: { runs: RunSummary[] }) {
  return (
    <Table className="text-sm">
      <TableHeader>
        <TableRow>
          <TableHead className="w-16">Run</TableHead>
          <TableHead>DAG</TableHead>
          <TableHead>Nodes</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Started</TableHead>
          <TableHead className="text-right">Duration</TableHead>
          <TableHead className="text-right">Processed</TableHead>
          <TableHead className="text-right">Cached</TableHead>
          <TableHead className="text-right">Failed</TableHead>
          <TableHead className="w-24" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {runs.map((run) => (
          <TableRow key={run.id}>
            <TableCell>
              <Link to="/runs/$runId" params={{ runId: String(run.id) }} className="font-mono hover:underline">
                #{run.id}
              </Link>
            </TableCell>
            <TableCell>{run.dag}</TableCell>
            <TableCell className="max-w-64">
              <div className="flex items-center gap-1.5">
                <RunKindBadge kind={run.kind} />
                <span className="truncate">
                  {run.kind === "single_doc" && run.doc_filename ? run.doc_filename : run.nodes.join(", ")}
                </span>
              </div>
            </TableCell>
            <TableCell>
              <div className="flex items-center gap-1.5">
                <RunStatusBadge status={run.status} />
                <ScratchBadge scratch={run.scratch} />
              </div>
            </TableCell>
            <TableCell title={formatDateTime(run.started_at ?? run.created_at)}>
              {formatRelative(run.started_at ?? run.created_at)}
            </TableCell>
            <TableCell className="text-right tabular-nums">{formatDuration(run.duration_s)}</TableCell>
            <TableCell className="text-right tabular-nums">{run.processed}</TableCell>
            <TableCell className="text-right tabular-nums">{run.cached}</TableCell>
            <TableCell className={`text-right tabular-nums ${run.failed > 0 ? "text-destructive" : ""}`}>
              {run.failed}
            </TableCell>
            <TableCell className="text-right">
              <CancelRunButton run={run} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
