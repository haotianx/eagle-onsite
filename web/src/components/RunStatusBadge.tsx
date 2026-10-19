import type { RunStatus } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const STATUSES: Record<RunStatus, { label: string; className: string }> = {
  queued: { label: "Queued", className: "border-border bg-muted text-muted-foreground" },
  running: { label: "Running", className: "border-violet-200 bg-violet-50 text-violet-700" },
  succeeded: { label: "Succeeded", className: "border-emerald-200 bg-emerald-50 text-emerald-700" },
  failed: { label: "Failed", className: "border-red-200 bg-red-50 text-red-700" },
  cancelled: { label: "Cancelled", className: "border-amber-200 bg-amber-50 text-amber-700" },
};

export function RunStatusBadge({ status, className }: { status: RunStatus; className?: string }) {
  const { label, className: color } = STATUSES[status];
  return (
    <Badge variant="outline" className={cn(color, className)}>
      {label}
    </Badge>
  );
}
