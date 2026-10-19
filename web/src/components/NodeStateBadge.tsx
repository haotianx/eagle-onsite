import type { StateName } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const STATES: Record<StateName, { label: string; className: string }> = {
  fresh: { label: "Fresh", className: "border-emerald-200 bg-emerald-50 text-emerald-700" },
  stale: { label: "Stale", className: "border-amber-200 bg-amber-50 text-amber-700" },
  candidate: { label: "Candidate", className: "border-sky-200 bg-sky-50 text-sky-700" },
  running: { label: "Running", className: "border-violet-200 bg-violet-50 text-violet-700" },
  failed: { label: "Failed", className: "border-red-200 bg-red-50 text-red-700" },
  never_run: { label: "Never run", className: "border-border bg-muted text-muted-foreground" },
};

export function NodeStateBadge({ state, className }: { state: StateName; className?: string }) {
  const { label, className: color } = STATES[state];
  return (
    <Badge variant="outline" className={cn(color, className)}>
      {label}
    </Badge>
  );
}
