import type { VersionSummary } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { HoverCard, HoverCardContent, HoverCardTrigger } from "@/components/ui/hover-card";
import { formatDateTime, shortSha } from "@/lib/format";
import { cn } from "@/lib/utils";

export type VersionRole = "current" | "candidate" | "scratch" | "other";

/** current and scratch follow from the version; candidates are known only from the node (NodeInfo.candidates). */
export function versionRole(version: VersionSummary, candidateIds?: Iterable<number>): VersionRole {
  if (version.current) return "current";
  if (version.scratch) return "scratch";
  if (candidateIds && new Set(candidateIds).has(version.id)) return "candidate";
  return "other";
}

const ROLE_BADGE: Record<Exclude<VersionRole, "other">, { label: string; className: string }> = {
  current: { label: "Current", className: "border-emerald-200 bg-emerald-50 text-emerald-700" },
  candidate: { label: "Candidate", className: "border-sky-200 bg-sky-50 text-sky-700" },
  scratch: { label: "Scratch", className: "border-amber-200 bg-amber-50 text-amber-700" },
};

export function VersionRoleBadge({ role, className }: { role: VersionRole; className?: string }) {
  if (role === "other") return null;
  const { label, className: color } = ROLE_BADGE[role];
  return (
    <Badge variant="outline" className={cn(color, className)}>
      {label}
    </Badge>
  );
}

export interface VersionLabelProps {
  version: VersionSummary;
  /** Overrides the role derived from the version (pass "candidate" for candidates). */
  role?: VersionRole;
  className?: string;
}

/** `dag/node@vN` with its role badge; details on hover. */
export function VersionLabel({ version, role, className }: VersionLabelProps) {
  const resolved = role ?? versionRole(version);
  return (
    <HoverCard openDelay={300}>
      <HoverCardTrigger asChild>
        <span className={cn("inline-flex items-center gap-1.5", className)}>
          <span className="font-mono text-sm">{version.label}</span>
          <VersionRoleBadge role={resolved} />
        </span>
      </HoverCardTrigger>
      <HoverCardContent className="w-72 text-xs">
        <VersionDetails version={version} />
      </HoverCardContent>
    </HoverCard>
  );
}

export function VersionDetails({ version }: { version: VersionSummary }) {
  const rows: [string, string][] = [
    ["Fingerprint", shortSha(version.node_fp, 12)],
    ["Commit", version.commit ? `${shortSha(version.commit)}${version.dirty ? " + dirty" : ""}` : version.dirty ? "dirty" : "—"],
    ["Snapshot", `#${version.snapshot_id}`],
    ["Created", formatDateTime(version.created_at)],
  ];
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
      {rows.map(([term, value]) => (
        <div key={term} className="contents">
          <dt className="text-muted-foreground">{term}</dt>
          <dd className="font-mono">{value}</dd>
        </div>
      ))}
    </dl>
  );
}
