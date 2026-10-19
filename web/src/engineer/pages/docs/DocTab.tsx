import { debugFileUrl, useRunLogs, useVersion, useVersionDoc } from "@/api/hooks";
import type { DebugFile, DocStatus, LogLine, VersionDocDetail } from "@/api/types";
import { IssueList } from "@/components/IssueList";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { formatBytes, formatDuration } from "@/lib/format";
import { cn } from "@/lib/utils";

import { RecordView, type RegionHandlers } from "./RecordView";

export interface DocTabProps extends RegionHandlers {
  node: string;
  docId: string;
  versionId: number;
}

/** One node's output for the doc in one version. */
export function DocTab({ node, docId, versionId, onHover, onSelect }: DocTabProps) {
  const detail = useVersionDoc(versionId, docId);
  if (detail.isPending) return <Skeleton className="h-40 w-full" />;
  if (detail.isError) return <p className="text-sm text-destructive">{detail.error.message}</p>;
  const d = detail.data;
  return (
    <div className="space-y-4">
      <Summary detail={d} />
      {d.status === "error" && <Failure detail={d} />}
      {d.issue_list.length > 0 && (
        <Section title="Issues">
          <IssueList issues={d.issue_list} />
        </Section>
      )}
      {d.version.scratch && d.debug_files.length > 0 && (
        <DebugFiles versionId={versionId} docId={docId} files={d.debug_files} />
      )}
      <Logs runId={d.version.run_id} node={node} docId={docId} />
      {d.record != null && <Record detail={d} versionId={versionId} onHover={onHover} onSelect={onSelect} />}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-1.5">
      <h3 className="text-xs font-medium text-muted-foreground">{title}</h3>
      {children}
    </section>
  );
}

const STATUS: Record<DocStatus, { label: string; className: string }> = {
  ok: { label: "OK", className: "border-emerald-200 bg-emerald-50 text-emerald-700" },
  error: { label: "Failed", className: "border-red-200 bg-red-50 text-red-700" },
  skipped: { label: "Skipped", className: "" },
};

function Summary({ detail }: { detail: VersionDocDetail }) {
  const status = STATUS[detail.status];
  const rows: [string, string][] = [
    ["Duration", formatDuration(detail.duration_s)],
    ["Peak memory", detail.peak_rss_mb == null ? "—" : formatBytes(detail.peak_rss_mb * 1024 * 1024)],
    ["Worker", detail.worker_pid == null ? "—" : `pid ${detail.worker_pid}`],
  ];
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs">
      <span className="flex items-center gap-1.5">
        <Badge variant="outline" className={status.className}>
          {status.label}
        </Badge>
        {detail.cached && <Badge variant="outline">Cached</Badge>}
      </span>
      {rows.map(([term, value]) => (
        <span key={term}>
          <span className="text-muted-foreground">{term} </span>
          <span className="tabular-nums">{value}</span>
        </span>
      ))}
    </div>
  );
}

function Failure({ detail }: { detail: VersionDocDetail }) {
  return (
    <Section title="Error">
      <p className="text-sm">
        <span className="font-mono font-medium text-destructive">{detail.error_type ?? "Error"}</span>
        {detail.error_message && <span>: {detail.error_message}</span>}
      </p>
      {detail.traceback && (
        <pre className="max-h-96 overflow-auto rounded-md bg-muted p-2 font-mono text-xs whitespace-pre">
          {detail.traceback}
        </pre>
      )}
    </Section>
  );
}

const IMAGE = /\.(png|jpe?g|gif|webp|svg)$/i;

function DebugFiles({ versionId, docId, files }: { versionId: number; docId: string; files: DebugFile[] }) {
  return (
    <Section title="Debug files">
      <ul className="space-y-2 text-xs">
        {files.map((file) => {
          const url = debugFileUrl(versionId, docId, file.name);
          return (
            <li key={file.name}>
              <a href={url} target="_blank" rel="noreferrer" className="font-mono hover:underline">
                {file.name}
              </a>
              <span className="ml-2 text-muted-foreground">{formatBytes(file.size)}</span>
              {IMAGE.test(file.name) && (
                <a href={url} target="_blank" rel="noreferrer" className="mt-1 block">
                  <img src={url} alt={file.name} className="max-h-64 rounded border" />
                </a>
              )}
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

function Logs({ runId, node, docId }: { runId: number | null; node: string; docId: string }) {
  const logs = useRunLogs(runId ?? undefined, { node, doc: docId });
  if (runId == null) return null;
  return (
    <Section title="Logs">
      {logs.isPending ? (
        <Skeleton className="h-16 w-full" />
      ) : logs.isError ? (
        <p className="text-xs text-destructive">{logs.error.message}</p>
      ) : logs.data.length === 0 ? (
        <p className="text-xs text-muted-foreground">No logs</p>
      ) : (
        <div className="max-h-60 overflow-auto rounded-md border font-mono text-xs">
          {logs.data.map((line, i) => (
            <LogRow key={i} line={line} />
          ))}
        </div>
      )}
    </Section>
  );
}

const LEVEL_CLASS: Record<string, string> = {
  ERROR: "text-destructive",
  CRITICAL: "text-destructive",
  WARNING: "text-amber-700",
};

function LogRow({ line }: { line: LogLine }) {
  const time = line.ts.slice(11, 19);
  return (
    <div className="flex gap-2 px-2 py-0.5">
      <span className="shrink-0 text-muted-foreground">{time}</span>
      <span className={cn("w-14 shrink-0", LEVEL_CLASS[line.level.toUpperCase()])}>{line.level.toUpperCase()}</span>
      <span className="break-all whitespace-pre-wrap">{line.message}</span>
    </div>
  );
}

function Record({ detail, versionId, ...handlers }: { detail: VersionDocDetail; versionId: number } & RegionHandlers) {
  const type = detail.version.artifact_type;
  const version = useVersion(type === "embed" ? versionId : undefined);
  const describe = version.data?.meta.describe as Record<string, unknown> | undefined;
  const dim = typeof describe?.dim === "number" ? describe.dim : undefined;
  return <RecordView artifactType={type} record={detail.record} vectorDim={dim} {...handlers} />;
}
