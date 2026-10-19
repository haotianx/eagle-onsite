import { cn } from "@/lib/utils";

export interface DocNameProps {
  doc: {
    doc_id?: string;
    filename?: string | null;
    job_number?: string | null;
    project?: string | null;
  };
  className?: string;
}

/** Filename with "job # · project" beneath it. */
export function DocName({ doc, className }: DocNameProps) {
  const secondary = [doc.job_number, doc.project].filter(Boolean).join(" · ");
  return (
    <div className={cn("min-w-0", className)}>
      <div className="truncate">{doc.filename ?? doc.doc_id}</div>
      {secondary && <div className="truncate text-xs text-muted-foreground">{secondary}</div>}
    </div>
  );
}
