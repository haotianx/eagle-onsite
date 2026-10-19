export interface PromoteDialogProps {
  versionId: number;
  open: boolean;
  onOpenChange(open: boolean): void;
  onPromoted?(): void;
}

/** Promote confirmation for a version. */
export function PromoteDialog(_props: PromoteDialogProps) {
  return null;
}
