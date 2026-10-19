import { getRouteApi } from "@tanstack/react-router";
import { useEffect, useMemo } from "react";

import { useCompare, useDag, useVersion } from "@/api/hooks";
import type { ComparisonOut, DocChangeOut } from "@/api/types";
import { DocName } from "@/components/DocName";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { VersionPicker } from "@/components/VersionPicker";
import { cn } from "@/lib/utils";

import { MetricCard } from "../versions/MetricCards";
import { PromoteButton } from "../versions/PromoteButton";
import { formatCount, formatDelta, summaryMetrics } from "../versions/summaryMetrics";
import { DocSideBySide } from "./DocSideBySide";
import { sortChanges } from "./docDiff";

const route = getRouteApi("/compare");

export function ComparePage() {
  const { a, b, doc } = route.useSearch();
  const navigate = route.useNavigate();
  const anchor = useVersion(a ?? b);
  const dag = anchor.data?.dag;
  const node = anchor.data?.node;
  const dagDetail = useDag(a == null || b == null ? dag : undefined);
  const versionB = useVersion(b);
  const comparison = useCompare(a, b);

  // With one side given: b defaults to the newest candidate, a to the current version.
  useEffect(() => {
    if ((a == null) === (b == null)) return;
    const info = dagDetail.data?.nodes.find((n) => n.name === node);
    if (!info) return;
    if (b == null) {
      const newest = info.candidates.filter((v) => v.id !== a).sort((x, y) => y.id - x.id)[0];
      if (newest) void navigate({ search: (prev) => ({ ...prev, b: newest.id }), replace: true });
    } else if (info.current && info.current.id !== b) {
      const current = info.current.id;
      void navigate({ search: (prev) => ({ ...prev, a: current }), replace: true });
    }
  }, [a, b, node, dagDetail.data, navigate]);

  function pick(side: "a" | "b", id: number) {
    void navigate({ search: (prev) => ({ ...prev, [side]: id }) });
  }

  if (a == null && b == null) return <p className="p-4 text-sm text-muted-foreground">No versions selected</p>;
  if (anchor.isError) return <p className="p-4 text-sm text-destructive">{anchor.error.message}</p>;

  return (
    <div className="flex flex-col gap-4 p-4">
      <header className="flex flex-wrap items-center gap-2">
        {dag && node ? (
          <>
            <span className="text-xs font-semibold text-muted-foreground">A</span>
            <VersionPicker dag={dag} node={node} value={a} onChange={(id) => pick("a", id)} className="w-64" />
            <span className="text-xs font-semibold text-muted-foreground">B</span>
            <VersionPicker dag={dag} node={node} value={b} onChange={(id) => pick("b", id)} className="w-64" />
          </>
        ) : (
          <Skeleton className="h-7 w-96" />
        )}
        <div className="ml-auto">{versionB.data && <PromoteButton version={versionB.data} />}</div>
      </header>

      {comparison.isError ? (
        <p className="text-sm text-destructive">{comparison.error.message}</p>
      ) : a == null || b == null ? null : !comparison.data ? (
        <Skeleton className="h-40" />
      ) : (
        <>
          <CompareMetrics comparison={comparison.data} />
          <div className="grid min-h-0 grid-cols-[minmax(20rem,26rem)_1fr] gap-4">
            <ChangedDocs
              changed={comparison.data.changed}
              unchanged={comparison.data.unchanged}
              selected={doc}
              onSelect={(docId) => void navigate({ search: (prev) => ({ ...prev, doc: docId }) })}
            />
            {doc ? (
              <DocSideBySide a={comparison.data.a} b={comparison.data.b} docId={doc} />
            ) : (
              <p className="py-6 text-center text-sm text-muted-foreground">No document selected</p>
            )}
          </div>
        </>
      )}
    </div>
  );
}

export function CompareMetrics({ comparison }: { comparison: ComparisonOut }) {
  const metrics = summaryMetrics(comparison.b.artifact_type ?? comparison.a.artifact_type, "compare");
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(10rem,1fr))] gap-2">
      {metrics.map((metric) => {
        const va = comparison.a_summary[metric.key];
        const vb = comparison.b_summary[metric.key];
        const delta = formatDelta(metric, va, vb);
        return (
          <MetricCard key={metric.key} label={metric.label} value={metric.format(vb)}>
            <div className="flex items-baseline justify-between gap-2 text-xs">
              <span className="truncate text-muted-foreground" title={metric.format(va)}>
                A {metric.format(va)}
              </span>
              {delta && <span className="shrink-0 font-medium tabular-nums">{delta}</span>}
            </div>
          </MetricCard>
        );
      })}
    </div>
  );
}

interface ChangedDocsProps {
  changed: DocChangeOut[];
  unchanged: number;
  selected: string | undefined;
  onSelect(docId: string): void;
}

export function ChangedDocs({ changed, unchanged, selected, onSelect }: ChangedDocsProps) {
  const rows = useMemo(() => sortChanges(changed), [changed]);
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div className="flex items-baseline justify-between text-sm">
        <span className="font-semibold">Changed docs</span>
        <span className="text-xs text-muted-foreground tabular-nums">
          {rows.length} changed · {unchanged} unchanged
        </span>
      </div>
      {rows.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">No changes</p>
      ) : (
        <Table className="text-sm">
          <TableHeader>
            <TableRow>
              <TableHead>Document</TableHead>
              <TableHead className="w-28 text-right">Records</TableHead>
              <TableHead className="w-16 text-right">Change</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow
                key={row.doc_id}
                aria-selected={row.doc_id === selected}
                className={cn("cursor-pointer", row.doc_id === selected && "bg-muted")}
                onClick={() => onSelect(row.doc_id)}
              >
                <TableCell className="max-w-0">
                  <DocName doc={row} />
                </TableCell>
                <TableCell className="text-right tabular-nums">{recordsText(row)}</TableCell>
                <TableCell className="text-right tabular-nums">{formatCount(row.change)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

export function recordsText(row: Pick<DocChangeOut, "a_records" | "b_records">): string {
  if (row.a_records == null && row.b_records != null) return "only in b";
  if (row.b_records == null && row.a_records != null) return "only in a";
  return `${formatCount(row.a_records)} → ${formatCount(row.b_records)}`;
}
