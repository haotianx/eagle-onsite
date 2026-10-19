import type { RunKind } from "@/api/types";
import { Badge } from "@/components/ui/badge";

/** "Single-doc" for single-doc runs; nothing for DAG runs. */
export function RunKindBadge({ kind, className }: { kind: RunKind; className?: string }) {
  if (kind !== "single_doc") return null;
  return (
    <Badge variant="secondary" className={className}>
      Single-doc
    </Badge>
  );
}
