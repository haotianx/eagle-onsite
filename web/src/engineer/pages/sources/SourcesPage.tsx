import { useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link } from "@tanstack/react-router";
import { Loader2 } from "lucide-react";
import { useState } from "react";

import { queryKeys, useSources, useSync } from "@/api/hooks";
import type { SourceOut } from "@/api/types";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime } from "@/lib/format";

import { SnapshotDiff } from "./SnapshotDiff";

const route = getRouteApi("/sources");

/** The selected source: by name, else the one holding the selected snapshot, else the first. */
function selectSource(sources: SourceOut[], name?: string, snapshot?: number): SourceOut | undefined {
  if (name != null) return sources.find((s) => s.name === name);
  if (snapshot != null) {
    const holder = sources.find((s) => s.snapshots.some((snap) => snap.id === snapshot));
    if (holder) return holder;
  }
  return sources[0];
}

export function SourcesPage() {
  const search = route.useSearch();
  const { data: sources, error, isPending } = useSources();

  let body;
  if (error) {
    body = (
      <Alert variant="destructive">
        <AlertDescription>{error.message}</AlertDescription>
      </Alert>
    );
  } else if (isPending) {
    body = <Skeleton className="h-24 w-full" />;
  } else {
    const source = selectSource(sources, search.source, search.snapshot);
    const snapshotId = search.snapshot ?? source?.snapshots[0]?.id;
    body = (
      <>
        <SourceTable sources={sources} selected={source?.name} />
        {sources.length > 0 &&
          (source ? (
            <div className="grid grid-cols-[minmax(0,24rem)_minmax(0,1fr)] items-start gap-4">
              <SnapshotTable source={source} selected={snapshotId} />
              {snapshotId != null && <SnapshotDiff snapshotId={snapshotId} />}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Source not found</p>
          ))}
      </>
    );
  }

  return (
    <div className="space-y-4 p-4">
      <h1 className="text-lg font-semibold">Sources</h1>
      {body}
    </div>
  );
}

function SourceTable({ sources, selected }: { sources: SourceOut[]; selected?: string }) {
  const navigate = route.useNavigate();
  if (sources.length === 0) return <p className="text-sm text-muted-foreground">No sources</p>;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Name</TableHead>
          <TableHead>Folders</TableHead>
          <TableHead>Glob</TableHead>
          <TableHead className="w-48" />
        </TableRow>
      </TableHeader>
      <TableBody>
        {sources.map((source) => (
          <TableRow
            key={source.name}
            data-state={source.name === selected ? "selected" : undefined}
            className="cursor-pointer"
            onClick={() => void navigate({ search: { source: source.name } })}
          >
            <TableCell className="font-medium">
              <Link to="/sources" search={{ source: source.name }}>
                {source.name}
              </Link>
            </TableCell>
            <TableCell className="font-mono text-xs">
              {source.folders.map((folder) => (
                <div key={folder}>{folder}</div>
              ))}
            </TableCell>
            <TableCell className="font-mono text-xs">{source.glob}</TableCell>
            <TableCell onClick={(e) => e.stopPropagation()}>
              <SyncButton source={source.name} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** Syncs a source, then selects the resulting snapshot. */
function SyncButton({ source }: { source: string }) {
  const navigate = route.useNavigate();
  const queryClient = useQueryClient();
  const sync = useSync();
  const [unchanged, setUnchanged] = useState(false);

  function onClick() {
    setUnchanged(false);
    sync.mutate(source, {
      onSuccess: (result) => {
        // The diff against the previous snapshot; when nothing was created it is empty and not cached.
        if (result.created) queryClient.setQueryData(queryKeys.snapshotDiff(result.snapshot.id), result.diff);
        setUnchanged(!result.created);
        void navigate({ search: { source, snapshot: result.snapshot.id } });
      },
    });
  }

  return (
    <div className="flex items-center justify-end gap-2">
      {unchanged && <span className="text-xs text-muted-foreground">No changes</span>}
      {sync.error && <span className="text-xs text-destructive">{sync.error.message}</span>}
      <Button size="sm" variant="outline" disabled={sync.isPending} onClick={onClick}>
        {sync.isPending && <Loader2 className="animate-spin" />}
        Sync
      </Button>
    </div>
  );
}

function SnapshotTable({ source, selected }: { source: SourceOut; selected?: number }) {
  const navigate = route.useNavigate();
  if (source.snapshots.length === 0) return <p className="text-sm text-muted-foreground">No snapshots</p>;
  return (
    <Table aria-label="Snapshots">
      <TableHeader>
        <TableRow>
          <TableHead>ID</TableHead>
          <TableHead>Created</TableHead>
          <TableHead className="text-right">Docs</TableHead>
          <TableHead className="text-right">Files</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {source.snapshots.map((snapshot) => (
          <TableRow
            key={snapshot.id}
            data-state={snapshot.id === selected ? "selected" : undefined}
            className="cursor-pointer"
            onClick={() => void navigate({ search: { source: source.name, snapshot: snapshot.id } })}
          >
            <TableCell>
              <Link to="/sources" search={{ source: source.name, snapshot: snapshot.id }}>
                #{snapshot.id}
              </Link>
            </TableCell>
            <TableCell>{formatDateTime(snapshot.created_at)}</TableCell>
            <TableCell className="text-right tabular-nums">{snapshot.doc_count}</TableCell>
            <TableCell className="text-right tabular-nums">{snapshot.file_count}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
