import { getRouteApi } from "@tanstack/react-router";
import { useState } from "react";

import { useVersionIssues, type VersionIssuesQuery } from "@/api/hooks";
import type { VersionDetail } from "@/api/types";
import { IssueList } from "@/components/IssueList";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

const route = getRouteApi("/versions/$versionId");

type Level = NonNullable<VersionIssuesQuery["level"]>;
type Source = NonNullable<VersionIssuesQuery["source"]>;

const LEVELS: { value: Level | "all"; label: string }[] = [
  { value: "all", label: "All levels" },
  { value: "error", label: "Errors" },
  { value: "warning", label: "Warnings" },
];

const SOURCES: { value: Source | "all"; label: string }[] = [
  { value: "all", label: "All sources" },
  { value: "hard", label: "Hard checks" },
  { value: "quality", label: "Quality checks" },
  { value: "script", label: "Script checks" },
];

/** The version's issues, filtered by level and source; a document opens the Document view. */
export function VersionIssues({ version }: { version: VersionDetail }) {
  const { level } = route.useSearch();
  const navigate = route.useNavigate();
  const [source, setSource] = useState<Source | undefined>();
  const issues = useVersionIssues(version.id, { level, source });

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Select
          value={level ?? "all"}
          onValueChange={(value) =>
            void navigate({
              search: (prev) => ({ ...prev, level: value === "all" ? undefined : (value as Level) }),
              replace: true,
            })
          }
        >
          <SelectTrigger size="sm" className="w-32" aria-label="Level">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {LEVELS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={source ?? "all"}
          onValueChange={(value) => setSource(value === "all" ? undefined : (value as Source))}
        >
          <SelectTrigger size="sm" className="w-36" aria-label="Source">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SOURCES.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {issues.isError ? (
        <p className="text-sm text-destructive">{issues.error.message}</p>
      ) : issues.isPending ? (
        <Skeleton className="h-40" />
      ) : issues.data.length === 0 ? (
        <p className="py-6 text-center text-sm text-muted-foreground">No issues</p>
      ) : (
        <IssueList
          issues={issues.data}
          onSelectDoc={(docId) =>
            void navigate({
              to: "/dags/$dag/docs/$docId",
              params: { dag: version.dag, docId },
              search: { node: version.node, version: version.id },
            })
          }
        />
      )}
    </div>
  );
}
