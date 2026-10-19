import { getRouteApi } from "@tanstack/react-router";
import { useState } from "react";

import { useVersionDocs } from "@/api/hooks";
import type { DocStatus, VersionDetail, VersionDocOut } from "@/api/types";
import { DocName } from "@/components/DocName";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDuration } from "@/lib/format";

import { docMetricsText } from "./summaryMetrics";

const route = getRouteApi("/versions/$versionId");

export const PAGE_SIZE = 50;

const STATUS_OPTIONS: { value: DocStatus | "all"; label: string }[] = [
  { value: "all", label: "All" },
  { value: "error", label: "Failed" },
  { value: "ok", label: "OK" },
  { value: "skipped", label: "Skipped" },
];

/** The version's documents, filtered by status and filename / job #, paged. */
export function VersionDocs({ version }: { version: VersionDetail }) {
  const { status, q } = route.useSearch();
  const navigate = route.useNavigate();
  const [offset, setOffset] = useState(0);
  const docs = useVersionDocs(version.id, { status, q, offset, limit: PAGE_SIZE });

  function setSearch(next: { status?: DocStatus; q?: string }) {
    setOffset(0);
    void navigate({ search: (prev) => ({ ...prev, ...next }), replace: true });
  }

  function open(doc: VersionDocOut) {
    void navigate({
      to: "/dags/$dag/docs/$docId",
      params: { dag: version.dag, docId: doc.doc_id },
      search: { node: version.node, version: version.id },
    });
  }

  const total = docs.data?.total ?? 0;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Select
          value={status ?? "all"}
          onValueChange={(value) => setSearch({ status: value === "all" ? undefined : (value as DocStatus) })}
        >
          <SelectTrigger size="sm" className="w-32" aria-label="Status">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STATUS_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          className="h-7 w-64"
          placeholder="Filename or job #"
          aria-label="Find document"
          defaultValue={q ?? ""}
          onChange={(e) => setSearch({ q: e.target.value || undefined })}
        />
        <span className="ml-auto text-xs text-muted-foreground tabular-nums">
          {total > 0 && `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} of ${total}`}
        </span>
        <Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(offset - PAGE_SIZE)}>
          Previous
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={offset + PAGE_SIZE >= total}
          onClick={() => setOffset(offset + PAGE_SIZE)}
        >
          Next
        </Button>
      </div>

      {docs.isError ? (
        <p className="text-sm text-destructive">{docs.error.message}</p>
      ) : docs.isPending ? (
        <Skeleton className="h-40" />
      ) : docs.data.items.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">No documents</p>
      ) : (
        <Table className="text-sm">
          <TableHeader>
            <TableRow>
              <TableHead>Document</TableHead>
              <TableHead className="w-24">Status</TableHead>
              <TableHead className="w-20">Cached</TableHead>
              <TableHead className="w-56">Records</TableHead>
              <TableHead className="w-24 text-right">Duration</TableHead>
              <TableHead className="w-28 text-right">Issues</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {docs.data.items.map((doc) => (
              <TableRow key={doc.doc_id} className="cursor-pointer" onClick={() => open(doc)}>
                <TableCell className="max-w-0">
                  <DocName doc={doc} />
                </TableCell>
                <TableCell>
                  <DocStatusBadge status={doc.status} />
                </TableCell>
                <TableCell className="text-muted-foreground">{doc.cached ? "Cached" : ""}</TableCell>
                <TableCell className="tabular-nums" title={doc.error_message ?? undefined}>
                  {doc.status === "ok" ? docMetricsText(version.artifact_type, doc.metrics) : (doc.error_type ?? "—")}
                </TableCell>
                <TableCell className="text-right tabular-nums">{formatDuration(doc.duration_s)}</TableCell>
                <TableCell className="text-right tabular-nums">
                  <IssueCountsText error={doc.issues.error ?? 0} warning={doc.issues.warning ?? 0} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

export function DocStatusBadge({ status }: { status: DocStatus }) {
  if (status === "error") return <Badge variant="destructive">Failed</Badge>;
  if (status === "skipped") return <Badge variant="outline">Skipped</Badge>;
  return (
    <Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-700">
      OK
    </Badge>
  );
}

function IssueCountsText({ error, warning }: { error: number; warning: number }) {
  if (!error && !warning) return <span className="text-muted-foreground">—</span>;
  return (
    <span className="inline-flex gap-2">
      {error > 0 && <span className="text-destructive">{error} E</span>}
      {warning > 0 && <span className="text-amber-700">{warning} W</span>}
    </span>
  );
}
