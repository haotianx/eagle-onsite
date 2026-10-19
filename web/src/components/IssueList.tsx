import type { IssueOut } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";

export interface IssueListProps {
  issues: IssueOut[];
  onSelectDoc?(docId: string): void;
  className?: string;
}

export function IssueList({ issues, onSelectDoc, className }: IssueListProps) {
  return (
    <Table className={cn("text-xs", className)}>
      <TableHeader>
        <TableRow>
          <TableHead className="w-20">Level</TableHead>
          <TableHead className="w-40">Code</TableHead>
          <TableHead>Message</TableHead>
          <TableHead className="w-56">Document</TableHead>
          <TableHead className="w-12 text-right">Page</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {issues.map((issue) => (
          <TableRow key={issue.id}>
            <TableCell>
              <LevelBadge level={issue.level} />
            </TableCell>
            <TableCell className="font-mono">{issue.code}</TableCell>
            <TableCell className="whitespace-normal">{issue.message}</TableCell>
            <TableCell className="max-w-56 truncate">
              {issue.doc_id == null ? null : onSelectDoc ? (
                <button
                  type="button"
                  className="max-w-full truncate text-left hover:underline"
                  onClick={() => onSelectDoc(issue.doc_id!)}
                >
                  {issue.filename ?? issue.doc_id}
                </button>
              ) : (
                (issue.filename ?? issue.doc_id)
              )}
            </TableCell>
            <TableCell className="text-right tabular-nums">{issue.page}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function LevelBadge({ level }: { level: string }) {
  if (level === "error") return <Badge variant="destructive">Error</Badge>;
  if (level === "warning")
    return (
      <Badge variant="outline" className="border-amber-200 bg-amber-50 text-amber-700">
        Warning
      </Badge>
    );
  return <Badge variant="outline">{level}</Badge>;
}
