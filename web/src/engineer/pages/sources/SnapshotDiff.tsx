import { ChevronRight } from "lucide-react";
import type { ReactNode } from "react";

import { useSnapshotDiff } from "@/api/hooks";
import type { DocRef, SnapshotDiffOut } from "@/api/types";
import { DocName } from "@/components/DocName";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Skeleton } from "@/components/ui/skeleton";

/** Diff of a snapshot against the previous snapshot of its source. */
export function SnapshotDiff({ snapshotId }: { snapshotId: number }) {
  const { data, error, isPending } = useSnapshotDiff(snapshotId);
  if (error) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{error.message}</AlertDescription>
      </Alert>
    );
  }
  if (isPending) return <Skeleton className="h-32 w-full" />;
  return <DiffView diff={data} />;
}

function DiffView({ diff }: { diff: SnapshotDiffOut }) {
  return (
    <div className="space-y-2" aria-label="Snapshot diff">
      <h2 className="text-sm font-medium">
        {diff.old ? `#${diff.old.id} → #${diff.new.id}` : `#${diff.new.id}`}
      </h2>
      <DiffSection title="Added" count={diff.added.length}>
        <DocList docs={diff.added} />
      </DiffSection>
      <DiffSection title="Removed" count={diff.removed.length}>
        <DocList docs={diff.removed} />
      </DiffSection>
      <DiffSection title="Changed" count={diff.changed_paths.length}>
        <ul className="space-y-1 font-mono text-xs">
          {diff.changed_paths.map((path) => (
            <li key={path} className="truncate" title={path}>
              {path}
            </li>
          ))}
        </ul>
      </DiffSection>
      <div className="flex gap-2 px-2 text-sm text-muted-foreground">
        <span>Unchanged</span>
        <span className="tabular-nums">{diff.unchanged}</span>
      </div>
    </div>
  );
}

function DiffSection({ title, count, children }: { title: string; count: number; children: ReactNode }) {
  return (
    <Collapsible defaultOpen={count > 0} disabled={count === 0} className="rounded-md border">
      <CollapsibleTrigger className="group flex w-full items-center gap-2 px-2 py-1.5 text-sm disabled:text-muted-foreground">
        <ChevronRight className="size-4 transition-transform group-data-[state=open]:rotate-90" />
        <span>{title}</span>
        <span className="tabular-nums text-muted-foreground">{count}</span>
      </CollapsibleTrigger>
      <CollapsibleContent className="border-t px-2 py-1.5">{children}</CollapsibleContent>
    </Collapsible>
  );
}

function DocList({ docs }: { docs: DocRef[] }) {
  return (
    <ul className="divide-y text-sm">
      {docs.map((doc) => (
        <li key={doc.doc_id} className="py-1" title={doc.path}>
          <DocName doc={doc} />
        </li>
      ))}
    </ul>
  );
}
