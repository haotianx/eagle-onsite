export interface RunLauncherProps {
  dag: string;
  node?: string;
  docIds?: string[];
  open: boolean;
  onOpenChange(open: boolean): void;
}

/** Run launcher drawer, preselected from where it's opened. */
export function RunLauncher(_props: RunLauncherProps) {
  return null;
}
