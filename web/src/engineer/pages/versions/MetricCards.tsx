import { cn } from "@/lib/utils";

import { summaryMetrics } from "./summaryMetrics";

export interface MetricCardsProps {
  artifactType: string | null | undefined;
  summary: Record<string, unknown>;
  className?: string;
}

/** A version's summary counts as cards. */
export function MetricCards({ artifactType, summary, className }: MetricCardsProps) {
  return (
    <div className={cn("grid grid-cols-[repeat(auto-fill,minmax(8.5rem,1fr))] gap-2", className)}>
      {summaryMetrics(artifactType, "version").map((metric) => (
        <MetricCard key={metric.key} label={metric.label} value={metric.format(summary[metric.key])} />
      ))}
    </div>
  );
}

export function MetricCard({ label, value, children }: { label: string; value: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-lg border px-3 py-2" role="group" aria-label={label}>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="truncate text-base font-semibold tabular-nums" title={value}>
        {value}
      </div>
      {children}
    </div>
  );
}
