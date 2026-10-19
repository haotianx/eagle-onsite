import { getRouteApi, Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import { queryKeys, useRun, useRunMetrics } from "@/api/hooks";
import type { RunDetail } from "@/api/types";
import { RunKindBadge } from "@/components/RunKindBadge";
import { RunStatusBadge } from "@/components/RunStatusBadge";
import { ScratchBadge } from "@/components/ScratchBadge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDateTime, formatDuration } from "@/lib/format";

import { CancelRunButton, isActive, usePolling } from "./common";
import { RunErrors } from "./RunErrors";
import { RunLogs } from "./RunLogs";
import { NodeTimings, SlowestDocs } from "./RunMetrics";
import { RunProgressPanel, useRunProgress } from "./RunProgressPanel";
import { commitLabel, RunVersions } from "./RunVersions";

const route = getRouteApi("/runs/$runId");

export function RunDetailPage() {
  const { runId } = route.useParams();
  const id = /^\d+$/.test(runId) ? Number(runId) : undefined;
  const run = useRun(id);

  if (id == null) return <p className="p-4 text-sm text-destructive">Run not found</p>;
  if (run.isPending) return <Skeleton className="m-4 h-40" />;
  if (run.isError) return <p className="p-4 text-sm text-destructive">{run.error.message}</p>;
  return <RunView key={id} run={run.data} />;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-sm font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function RunView({ run }: { run: RunDetail }) {
  const active = isActive(run.status);
  usePolling(active, queryKeys.run(run.id));
  const progress = useRunProgress(run.id, run.nodes, active);
  const metrics = useRunMetrics(run.id);

  const requested = run.request.snapshot_id;
  const snapshotId =
    progress.snapshotId ?? (typeof requested === "number" ? requested : undefined) ?? run.versions[0]?.snapshot_id;
  const nodes = progress.nodes.length > 0 ? progress.nodes.map((n) => n.node) : run.nodes;
  const versionIds = new Map(run.versions.map((v) => [v.node, v.id]));
  const docNames = new Map<string, string>();
  for (const d of metrics.data?.slowest ?? []) if (d.filename) docNames.set(d.doc_id, d.filename);

  return (
    <div className="flex flex-col gap-6 p-4">
      <header className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="font-mono text-lg font-semibold">Run #{run.id}</h1>
          <RunStatusBadge status={run.status} />
          <ScratchBadge scratch={run.scratch} />
          <RunKindBadge kind={run.kind} />
          {run.kind === "single_doc" && run.doc_filename && <span className="text-sm">{run.doc_filename}</span>}
          <span className="ml-auto">
            <CancelRunButton run={run} />
          </span>
        </div>
        <dl className="grid grid-cols-[repeat(auto-fit,minmax(9rem,max-content))] gap-x-8 gap-y-1 text-sm">
          <Field term="DAG">
            <Link to="/dags/$dag" params={{ dag: run.dag }} className="hover:underline">
              {run.dag}
            </Link>
          </Field>
          <Field term="Nodes">{run.nodes.join(", ") || "—"}</Field>
          <Field term="Commit">
            <span className="font-mono">{commitLabel(run.commit, run.dirty)}</span>
          </Field>
          <Field term="Snapshot">{snapshotId == null ? "—" : `#${snapshotId}`}</Field>
          <Field term="Started">{formatDateTime(run.started_at ?? run.created_at)}</Field>
          <Field term="Duration">{formatDuration(run.duration_s)}</Field>
        </dl>
        {run.status === "failed" && run.error && (
          <Alert variant="destructive">
            <AlertDescription className="font-mono text-xs whitespace-pre-wrap">{run.error}</AlertDescription>
          </Alert>
        )}
      </header>

      <Section title="Progress">
        <RunProgressPanel progress={progress} />
      </Section>

      <Section title="Versions">
        <RunVersions versions={run.versions} />
      </Section>

      <Section title="Errors">
        <RunErrors versions={run.versions} />
      </Section>

      <Section title="Timings">
        {metrics.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : metrics.isError ? (
          <p className="text-sm text-destructive">{metrics.error.message}</p>
        ) : (
          <>
            <NodeTimings metrics={metrics.data} nodes={nodes} />
            <h3 className="mt-2 text-sm font-medium">Slowest documents</h3>
            <SlowestDocs metrics={metrics.data} dag={run.dag} nodes={nodes} versionIds={versionIds} />
          </>
        )}
      </Section>

      <Section title="Logs">
        <RunLogs runId={run.id} nodes={nodes} docNames={docNames} />
      </Section>
    </div>
  );
}

function Field({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="flex flex-col">
      <dt className="text-xs text-muted-foreground">{term}</dt>
      <dd>{children}</dd>
    </div>
  );
}
