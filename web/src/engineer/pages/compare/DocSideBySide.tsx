import { useMemo } from "react";

import { useCompareDoc } from "@/api/hooks";
import type { VersionSummary } from "@/api/types";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import { docDiffView, type ChunkRow, type TextElement } from "./docDiff";

const CHANGED = "bg-amber-50 shadow-[inset_2px_0_0_var(--color-amber-500)] dark:bg-amber-950/40";

export interface DocSideBySideProps {
  a: VersionSummary;
  b: VersionSummary;
  docId: string;
}

/** One doc's records in versions a and b, side by side, with changed rows or elements marked. */
export function DocSideBySide({ a, b, docId }: DocSideBySideProps) {
  const diff = useCompareDoc(a.id, b.id, docId);
  const view = useMemo(() => (diff.data ? docDiffView(diff.data) : null), [diff.data]);

  if (diff.isError) return <p className="text-sm text-destructive">{diff.error.message}</p>;
  if (!view) return <Skeleton className="h-60" />;

  const sides = [
    { version: a, value: view.a },
    { version: b, value: view.b },
  ];

  return (
    <div className="grid min-h-0 grid-cols-2 gap-3">
      {sides.map(({ version, value }, i) => (
        <div key={i} className="flex min-w-0 flex-col gap-1">
          <div className="font-mono text-xs text-muted-foreground">{version.label}</div>
          {value == null ? (
            <p className="py-6 text-center text-sm text-muted-foreground">Not in this version</p>
          ) : view.kind === "table" ? (
            <RowList rows={value as ChunkRow[]} />
          ) : view.kind === "texts" ? (
            <TextList elements={value as TextElement[]} />
          ) : (
            <pre className="overflow-auto rounded-md border p-2 font-mono text-xs">{JSON.stringify(value, null, 2)}</pre>
          )}
        </div>
      ))}
    </div>
  );
}

function RowList({ rows }: { rows: ChunkRow[] }) {
  return (
    <ol className="flex flex-col divide-y rounded-md border text-xs">
      {rows.map((row) => (
        <li key={row.key} data-changed={row.changed || undefined} className={cn("flex flex-col gap-0.5 p-2", row.changed && CHANGED)}>
          <div className="flex gap-2 text-muted-foreground">
            <span className="tabular-nums">#{String(row.seq)}</span>
            <span className="min-w-0 flex-1 truncate">{row.section}</span>
            <span className="shrink-0 tabular-nums">{row.pages && `p. ${row.pages}`}</span>
          </div>
          <p className="whitespace-pre-wrap">{row.text}</p>
        </li>
      ))}
    </ol>
  );
}

function TextList({ elements }: { elements: TextElement[] }) {
  return (
    <ol className="flex flex-col divide-y rounded-md border text-xs">
      {elements.map((element) => (
        <li
          key={element.key}
          data-changed={element.changed || undefined}
          className={cn("flex gap-2 p-2", element.changed && CHANGED)}
        >
          <span className="w-24 shrink-0 truncate text-muted-foreground">{element.label}</span>
          <p className="min-w-0 whitespace-pre-wrap">{element.text}</p>
        </li>
      ))}
    </ol>
  );
}
