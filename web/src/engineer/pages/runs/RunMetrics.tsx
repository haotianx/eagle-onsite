import { Link } from "@tanstack/react-router";

import type { RunMetricsOut } from "@/api/types";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatBytes, formatDuration, formatPercent } from "@/lib/format";

const NONE = "—";

function count(value: number | undefined): string {
  return value == null ? NONE : String(Math.round(value));
}

function rate(value: number | undefined): string {
  return value == null ? NONE : value.toFixed(value < 10 ? 2 : 1);
}

/** Orders node metrics by the run's node order; unknown nodes last. */
function ordered<T extends { node: string }>(items: T[], nodes: string[]): T[] {
  const index = (node: string) => {
    const i = nodes.indexOf(node);
    return i === -1 ? nodes.length : i;
  };
  return [...items].sort((a, b) => index(a.node) - index(b.node));
}

export function NodeTimings({ metrics, nodes }: { metrics: RunMetricsOut; nodes: string[] }) {
  if (metrics.nodes.length === 0) return <p className="text-sm text-muted-foreground">No metrics</p>;
  return (
    <Table className="text-sm">
      <TableHeader>
        <TableRow>
          <TableHead>Node</TableHead>
          <TableHead className="text-right">Wall</TableHead>
          <TableHead className="text-right">Processed</TableHead>
          <TableHead className="text-right">Cached</TableHead>
          <TableHead className="text-right">Failed</TableHead>
          <TableHead className="text-right">Cache hit</TableHead>
          <TableHead className="text-right">Failure rate</TableHead>
          <TableHead className="text-right">Pages/s</TableHead>
          <TableHead className="text-right">OCR&apos;d pages</TableHead>
          <TableHead className="text-right">Model load</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {ordered(metrics.nodes, nodes).map(({ node, metrics: m }) => (
          <TableRow key={node}>
            <TableCell className="font-mono">{node}</TableCell>
            <TableCell className="text-right tabular-nums">{formatDuration(m.wall_s)}</TableCell>
            <TableCell className="text-right tabular-nums">{count(m.docs_processed)}</TableCell>
            <TableCell className="text-right tabular-nums">{count(m.docs_cached)}</TableCell>
            <TableCell className="text-right tabular-nums">{count(m.docs_failed)}</TableCell>
            <TableCell className="text-right tabular-nums">{formatPercent(m.cache_hit_rate)}</TableCell>
            <TableCell className="text-right tabular-nums">{formatPercent(m.failure_rate)}</TableCell>
            <TableCell className="text-right tabular-nums">{rate(m.pages_per_s)}</TableCell>
            <TableCell className="text-right tabular-nums">
              {m.ocr_pages == null ? NONE : m.pages != null ? `${count(m.ocr_pages)} / ${count(m.pages)}` : count(m.ocr_pages)}
            </TableCell>
            <TableCell className="text-right tabular-nums">{formatDuration(m.model_load_s)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

export interface SlowestDocsProps {
  metrics: RunMetricsOut;
  dag: string;
  nodes: string[];
  /** The run's version per node, for Document view links. */
  versionIds: Map<string, number>;
}

export function SlowestDocs({ metrics, dag, nodes, versionIds }: SlowestDocsProps) {
  if (metrics.slowest.length === 0) return <p className="text-sm text-muted-foreground">No timings</p>;
  return (
    <Table className="text-sm">
      <TableHeader>
        <TableRow>
          <TableHead className="w-24">Node</TableHead>
          <TableHead>Document</TableHead>
          <TableHead className="text-right">Duration</TableHead>
          <TableHead className="text-right">Peak memory</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {ordered(metrics.slowest, nodes).map((d) => (
          <TableRow key={`${d.node}/${d.doc_id}`}>
            <TableCell className="font-mono">{d.node}</TableCell>
            <TableCell className="max-w-96 truncate">
              <Link
                to="/dags/$dag/docs/$docId"
                params={{ dag, docId: d.doc_id }}
                search={{ node: d.node, version: versionIds.get(d.node) }}
                className="hover:underline"
              >
                {d.filename ?? d.doc_id}
              </Link>
            </TableCell>
            <TableCell className="text-right tabular-nums">{formatDuration(d.duration_s)}</TableCell>
            <TableCell className="text-right tabular-nums">
              {d.peak_rss_mb == null ? NONE : formatBytes(d.peak_rss_mb * 1024 * 1024)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
