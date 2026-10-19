import { XIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { PdfViewer } from "@/components/pdf/PdfViewer";
import { Button } from "@/components/ui/button";

import { type PdfTarget, usePdfPanel } from "../pdf-panel";

/** The right-hand panel: the PDF panel's target document, at its page with its regions highlighted. */
export function PdfPanel() {
  const { target, close } = usePdfPanel();
  const [position, setPosition] = useState<{ docId: string; page: number; numPages: number } | null>(null);
  // The viewer scrolls when its page or highlights change. A new target without highlights is shown
  // without a page for one render, so reopening the page already shown scrolls back to it.
  const [settled, setSettled] = useState<PdfTarget | null>(null);
  useEffect(() => setSettled(target), [target]);
  if (target == null) return null;

  const meta = [target.jobNumber, target.project].filter(Boolean).join(" · ");
  const page = position?.docId === target.docId ? position : null;
  const withPage = settled === target || (target.regions?.length ?? 0) > 0;

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-start gap-2 border-b py-2 pr-2 pl-4">
        <div className="min-w-0 flex-1 py-0.5">
          <div className="truncate text-sm font-medium">{target.filename ?? "Document"}</div>
          {meta && <div className="truncate text-xs text-muted-foreground">{meta}</div>}
        </div>
        {page && (
          <span className="shrink-0 py-1 text-xs text-muted-foreground tabular-nums">
            p. {page.page} / {page.numPages}
          </span>
        )}
        <Button variant="ghost" size="icon-sm" aria-label="Close" onClick={close}>
          <XIcon />
        </Button>
      </div>
      <PdfViewer
        className="min-h-0 flex-1"
        docId={target.docId}
        page={withPage ? (target.page ?? 1) : undefined}
        highlights={target.regions}
        onPageChange={(n, numPages) => setPosition({ docId: target.docId, page: n, numPages })}
      />
    </div>
  );
}
