import { createContext, type ReactNode, useContext, useMemo, useState } from "react";

import type { Region } from "./api";

/** What the PDF panel shows: a document, opened at `page` with `regions` highlighted. */
export interface PdfTarget {
  docId: string;
  filename?: string | null;
  jobNumber?: string | null;
  project?: string | null;
  /** 1-based page to open at (default 1). */
  page?: number;
  regions?: Region[];
}

interface PdfPanelValue {
  target: PdfTarget | null;
  open(target: PdfTarget): void;
  close(): void;
}

const PdfPanelContext = createContext<PdfPanelValue | null>(null);

export function PdfPanelProvider({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<PdfTarget | null>(null);
  const value = useMemo<PdfPanelValue>(() => ({ target, open: setTarget, close: () => setTarget(null) }), [target]);
  return <PdfPanelContext.Provider value={value}>{children}</PdfPanelContext.Provider>;
}

/** The right-hand PDF panel: citations, search-step passages and document cards open it. */
export function usePdfPanel(): PdfPanelValue {
  const value = useContext(PdfPanelContext);
  if (value == null) throw new Error("usePdfPanel needs a PdfPanelProvider");
  return value;
}
