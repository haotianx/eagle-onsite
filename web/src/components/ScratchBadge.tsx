import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/** Whether a run or version is scratch (never promotable) or promotable. */
export function ScratchBadge({ scratch, className }: { scratch: boolean; className?: string }) {
  return (
    <Badge
      variant="outline"
      className={cn(
        scratch ? "border-amber-200 bg-amber-50 text-amber-700" : "border-emerald-200 bg-emerald-50 text-emerald-700",
        className,
      )}
    >
      {scratch ? "Scratch" : "Promotable"}
    </Badge>
  );
}
