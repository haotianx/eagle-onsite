import { useQueryClient, type QueryKey } from "@tanstack/react-query";
import { useEffect } from "react";
import { toast } from "sonner";

import { useCancelRun } from "@/api/hooks";
import type { RunStatus, RunSummary } from "@/api/types";
import { Button } from "@/components/ui/button";

export const POLL_MS = 2000;

export function isActive(status: RunStatus): boolean {
  return status === "queued" || status === "running";
}

/** Invalidates `queryKey` (and everything below it) every POLL_MS while `active`. */
export function usePolling(active: boolean, queryKey: QueryKey): void {
  const client = useQueryClient();
  const key = JSON.stringify(queryKey);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => {
      void client.invalidateQueries({ queryKey: JSON.parse(key) as QueryKey });
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [active, key, client]);
}

export function CancelRunButton({ run }: { run: RunSummary }) {
  const cancel = useCancelRun();
  if (!isActive(run.status)) return null;
  const pending = cancel.isPending || run.cancel_requested;
  return (
    <Button
      variant="outline"
      size="xs"
      disabled={pending}
      onClick={() =>
        cancel.mutate(run.id, { onError: (error) => toast.error(error.message) })
      }
    >
      {run.cancel_requested ? "Cancelling" : "Cancel"}
    </Button>
  );
}
