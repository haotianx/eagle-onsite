import { toast } from "sonner";

import { usePromote, useVersion, useVersionIssues } from "@/api/hooks";
import { IssueList } from "@/components/IssueList";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";

export interface PromoteDialogProps {
  versionId: number;
  open: boolean;
  onOpenChange(open: boolean): void;
  onPromoted?(): void;
}

/** Promote confirmation for a version. */
export function PromoteDialog({ versionId, open, onOpenChange, onPromoted }: PromoteDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        {open && <PromoteBody versionId={versionId} onOpenChange={onOpenChange} onPromoted={onPromoted} />}
      </DialogContent>
    </Dialog>
  );
}

function PromoteBody({ versionId, onOpenChange, onPromoted }: Omit<PromoteDialogProps, "open">) {
  const version = useVersion(versionId);
  const issues = useVersionIssues(versionId);
  const promote = usePromote();

  const v = version.data;

  function confirm() {
    promote.mutate(versionId, {
      onSuccess: (result) => {
        toast.success(`Promoted ${result.version.label}`, {
          description: result.warnings.length ? result.warnings.join("\n") : undefined,
        });
        onPromoted?.();
        onOpenChange(false);
      },
    });
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>Promote {v ? <span className="font-mono">{v.label}</span> : null}</DialogTitle>
        <DialogDescription>
          {!v ? (
            <Skeleton className="h-4 w-48" />
          ) : v.replaces ? (
            <>
              Replaces <span className="font-mono">{v.replaces.label}</span>
            </>
          ) : (
            "No current version"
          )}
        </DialogDescription>
      </DialogHeader>

      {version.isError && <p className="text-sm text-destructive">{version.error.message}</p>}

      {v && (
        <div className="flex min-w-0 flex-col gap-3 text-sm">
          {!v.promotable && (
            <Alert variant="destructive">
              <AlertTitle>Not promotable</AlertTitle>
              <AlertDescription>
                <ul className="list-disc pl-4">
                  {v.not_promotable_reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              </AlertDescription>
            </Alert>
          )}
          {v.noncurrent_inputs.length > 0 && (
            <Alert>
              <AlertTitle>Built from inputs that aren't current</AlertTitle>
              <AlertDescription>{v.noncurrent_inputs.join(", ")}</AlertDescription>
            </Alert>
          )}
          <div className="flex items-center gap-3">
            <span className="font-medium">Issues</span>
            <span className={v.issues.error ? "text-destructive" : "text-muted-foreground"}>
              {v.issues.error} {v.issues.error === 1 ? "error" : "errors"}
            </span>
            <span className={v.issues.warning ? "text-amber-700" : "text-muted-foreground"}>
              {v.issues.warning} {v.issues.warning === 1 ? "warning" : "warnings"}
            </span>
          </div>
          {issues.data && issues.data.length > 0 && (
            <div className="max-h-64 overflow-auto rounded-md border">
              <IssueList issues={issues.data} />
            </div>
          )}
        </div>
      )}

      {promote.isError && (
        <p role="alert" className="text-sm text-destructive">
          {promote.error.message}
        </p>
      )}

      <DialogFooter>
        <Button variant="outline" onClick={() => onOpenChange(false)}>
          Cancel
        </Button>
        <Button onClick={confirm} disabled={!v?.promotable || promote.isPending}>
          Promote
        </Button>
      </DialogFooter>
    </>
  );
}
