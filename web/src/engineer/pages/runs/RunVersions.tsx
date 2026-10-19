import { Link } from "@tanstack/react-router";

import type { VersionSummary, VersionDetail } from "@/api/types";
import { VersionLabel } from "@/components/VersionLabel";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { shortSha } from "@/lib/format";

function VersionLink({ version }: { version: VersionSummary }) {
  return (
    <Link to="/versions/$versionId" params={{ versionId: String(version.id) }} className="hover:underline">
      <VersionLabel version={version} />
    </Link>
  );
}

export function commitLabel(commit: string | null, dirty: boolean): string {
  if (!commit) return dirty ? "dirty" : "—";
  return `${shortSha(commit)}${dirty ? " + dirty" : ""}`;
}

/** Versions the run produced with their lineage. */
export function RunVersions({ versions }: { versions: VersionDetail[] }) {
  if (versions.length === 0) return <p className="text-sm text-muted-foreground">No versions</p>;
  return (
    <Table className="text-sm">
      <TableHeader>
        <TableRow>
          <TableHead>Version</TableHead>
          <TableHead className="text-right">Docs</TableHead>
          <TableHead className="text-right">Failed</TableHead>
          <TableHead>Inputs</TableHead>
          <TableHead>Commit</TableHead>
          <TableHead>Snapshot</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {versions.map((v) => (
          <TableRow key={v.id}>
            <TableCell>
              <VersionLink version={v} />
            </TableCell>
            <TableCell className="text-right tabular-nums">{v.docs}</TableCell>
            <TableCell className={`text-right tabular-nums ${v.failed > 0 ? "text-destructive" : ""}`}>
              {v.failed}
            </TableCell>
            <TableCell>
              {v.inputs.length === 0 ? (
                <span className="text-muted-foreground">—</span>
              ) : (
                <div className="flex flex-col gap-1">
                  {v.inputs.map((input) => (
                    <span key={input.node} className="flex items-center gap-1.5">
                      <VersionLink version={input.version} />
                      {!input.current && (
                        <Badge variant="outline" className="border-amber-200 bg-amber-50 text-amber-700">
                          Not current
                        </Badge>
                      )}
                    </span>
                  ))}
                </div>
              )}
            </TableCell>
            <TableCell className="font-mono">{commitLabel(v.commit, v.dirty)}</TableCell>
            <TableCell>#{v.snapshot_id}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
