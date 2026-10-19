import { getRouteApi } from "@tanstack/react-router";
import { XIcon } from "lucide-react";
import { useState } from "react";

import { useRunLogs, type RunLogsQuery } from "@/api/hooks";
import type { LogLine } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";

const route = getRouteApi("/runs/$runId");

const ALL = "__all__";
export const LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"] as const;

const time = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  fractionalSecondDigits: 3,
  hour12: false,
});

function formatTime(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : time.format(date);
}

interface Filters {
  node?: string;
  doc?: string;
  level?: string;
}

export interface RunLogsProps {
  runId: number;
  nodes: string[];
  /** Filenames by doc id, where known. */
  docNames: Map<string, string>;
}

export function RunLogs({ runId, nodes, docNames }: RunLogsProps) {
  const { node, doc, level } = route.useSearch();
  const navigate = route.useNavigate();
  const query: RunLogsQuery = { node, doc, level };
  const logs = useRunLogs(runId, query);

  const setFilter = (patch: Filters) =>
    void navigate({ search: (prev) => ({ ...prev, ...patch }), replace: true });

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={node ?? ALL} onValueChange={(v) => setFilter({ node: v === ALL ? undefined : v })}>
          <SelectTrigger size="sm" aria-label="Node" className="min-w-32">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All nodes</SelectItem>
            {node && !nodes.includes(node) && <SelectItem value={node}>{node}</SelectItem>}
            {nodes.map((n) => (
              <SelectItem key={n} value={n}>
                {n}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={level ?? ALL} onValueChange={(v) => setFilter({ level: v === ALL ? undefined : v })}>
          <SelectTrigger size="sm" aria-label="Level" className="min-w-28">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>All levels</SelectItem>
            {LEVELS.map((l) => (
              <SelectItem key={l} value={l}>
                {l}+
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <DocFilter key={doc ?? ""} doc={doc} label={doc ? docNames.get(doc) : undefined} onChange={(d) => setFilter({ doc: d })} />
      </div>
      {logs.isPending ? (
        <Skeleton className="h-32 w-full" />
      ) : logs.isError ? (
        <p className="text-sm text-destructive">{logs.error.message}</p>
      ) : logs.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">No log lines</p>
      ) : (
        <LogTable lines={logs.data} docNames={docNames} onSelectDoc={(d) => setFilter({ doc: d })} />
      )}
    </div>
  );
}

function DocFilter({
  doc,
  label,
  onChange,
}: {
  doc: string | undefined;
  label: string | undefined;
  onChange(doc: string | undefined): void;
}) {
  const [value, setValue] = useState(doc ?? "");
  const commit = () => {
    const next = value.trim() || undefined;
    if (next !== doc) onChange(next);
  };
  return (
    <div className="flex items-center gap-1">
      <Input
        aria-label="Document"
        placeholder="Doc id"
        className="h-7 w-56 font-mono text-xs"
        value={value}
        title={label}
        onChange={(e) => setValue(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
        }}
      />
      {label && <span className="max-w-48 truncate text-xs text-muted-foreground">{label}</span>}
      {doc && (
        <Button variant="ghost" size="icon-xs" aria-label="Clear document" onClick={() => onChange(undefined)}>
          <XIcon />
        </Button>
      )}
    </div>
  );
}

function LevelBadge({ level }: { level: string }) {
  const className =
    level === "ERROR" || level === "CRITICAL"
      ? "border-red-200 bg-red-50 text-red-700"
      : level === "WARNING"
        ? "border-amber-200 bg-amber-50 text-amber-700"
        : "text-muted-foreground";
  return (
    <Badge variant="outline" className={className}>
      {level}
    </Badge>
  );
}

function LogTable({
  lines,
  docNames,
  onSelectDoc,
}: {
  lines: LogLine[];
  docNames: Map<string, string>;
  onSelectDoc(doc: string): void;
}) {
  return (
    <Table className="text-xs">
      <TableHeader>
        <TableRow>
          <TableHead className="w-28">Time</TableHead>
          <TableHead className="w-20">Level</TableHead>
          <TableHead className="w-24">Node</TableHead>
          <TableHead className="w-48">Document</TableHead>
          <TableHead>Message</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {lines.map((line, i) => {
          const isEvent = line.kind === "event";
          return (
            <TableRow key={i} data-kind={line.kind} className={cn(isEvent && "bg-muted/40")}>
              <TableCell className="font-mono tabular-nums text-muted-foreground">{formatTime(line.ts)}</TableCell>
              <TableCell>
                <LevelBadge level={line.level} />
              </TableCell>
              <TableCell className="font-mono">{line.node}</TableCell>
              <TableCell className="max-w-48 truncate">
                {line.doc_id && (
                  <button
                    type="button"
                    className="max-w-full truncate text-left hover:underline"
                    title={line.doc_id}
                    onClick={() => onSelectDoc(line.doc_id!)}
                  >
                    {docNames.get(line.doc_id) ?? line.doc_id}
                  </button>
                )}
              </TableCell>
              <TableCell className="font-mono whitespace-pre-wrap break-all">
                {isEvent ? (
                  <span className="flex items-baseline gap-2">
                    <Badge variant="secondary" className="font-mono">
                      {line.event}
                    </Badge>
                    {line.message && line.message !== line.event?.replace(/_/g, " ") && <span>{line.message}</span>}
                  </span>
                ) : (
                  <>
                    {line.kind !== "log" && <span className="mr-2 text-muted-foreground">{line.kind}</span>}
                    {line.message}
                  </>
                )}
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
