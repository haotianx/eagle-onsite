import { type ComponentProps, type ReactNode, type RefObject, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";

import { docPdfUrl } from "@/api/hooks";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import { regionToRect, type Region } from "./regions";

export { regionToRect, type Rect, type Region } from "./regions";

pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();

type PdfDocument = Parameters<NonNullable<ComponentProps<typeof Document>["onLoadSuccess"]>>[0];

interface PageSize {
  width: number;
  height: number;
}

export interface PdfViewerProps {
  docId: string;
  /** 1-based page to scroll to whenever it changes. */
  page?: number;
  onPageChange?(page: number, numPages: number): void;
  highlights?: Region[];
  className?: string;
}

type Load = { status: "loading" } | { status: "ready"; data: Uint8Array } | { status: "error"; message: string };

const ERRORS: Record<number, string> = {
  404: "Original changed or missing",
  410: "Document removed",
};

/** A document's original PDF: all pages in one vertical scroll, fit to width, with highlighted regions. */
export function PdfViewer({ docId, page, onPageChange, highlights, className }: PdfViewerProps) {
  const load = usePdfBytes(docId);
  const file = useMemo(() => (load.status === "ready" ? { data: load.data.slice() } : null), [load]);
  const scroller = useRef<HTMLDivElement>(null);
  const width = useWidth(scroller);
  const [sizes, setSizes] = useState<PageSize[] | null>(null);
  const [visible, setVisible] = useState<Set<number>>(() => new Set([1]));
  const slots = useRef(new Map<number, HTMLDivElement>());
  const current = useRef(0);
  const onPageChangeRef = useRef(onPageChange);
  useEffect(() => {
    onPageChangeRef.current = onPageChange;
  });

  useEffect(() => setSizes(null), [docId]);

  const onLoad = useCallback(async (pdf: PdfDocument) => {
    const pages = await Promise.all(
      Array.from({ length: pdf.numPages }, (_, i) =>
        pdf.getPage(i + 1).then((p) => {
          const viewport = p.getViewport({ scale: 1 });
          return { width: viewport.width, height: viewport.height };
        }),
      ),
    );
    current.current = 0;
    setSizes(pages);
  }, []);

  // Render pages near the viewport only.
  useEffect(() => {
    const root = scroller.current;
    if (!root || !sizes) return;
    const observer = new IntersectionObserver(
      (entries) =>
        setVisible((prev) => {
          const next = new Set(prev);
          for (const entry of entries) {
            const n = Number((entry.target as HTMLElement).dataset.page);
            if (entry.isIntersecting) next.add(n);
            else next.delete(n);
          }
          return next;
        }),
      { root, rootMargin: "100% 0px" },
    );
    for (const el of slots.current.values()) observer.observe(el);
    return () => observer.disconnect();
  }, [sizes, width]);

  const reportPage = useCallback(() => {
    const root = scroller.current;
    if (!root || !sizes) return;
    const probe = root.scrollTop + root.clientHeight / 3;
    let n = 1;
    for (const [num, el] of slots.current) if (el.offsetTop <= probe && num > n) n = num;
    if (n !== current.current) {
      current.current = n;
      onPageChangeRef.current?.(n, sizes.length);
    }
  }, [sizes]);

  useEffect(reportPage, [reportPage, width]);

  useEffect(() => {
    if (!sizes || page == null) return;
    const el = slots.current.get(Math.min(Math.max(page, 1), sizes.length));
    if (el && scroller.current) scroller.current.scrollTop = el.offsetTop;
  }, [page, sizes]);

  useEffect(() => {
    const first = highlights?.[0];
    const root = scroller.current;
    if (!first || !sizes || !root || !width) return;
    const el = slots.current.get(first.page);
    const size = sizes[first.page - 1];
    if (!el || !size) return;
    const rect = regionToRect(first, size.width, width);
    root.scrollTop = el.offsetTop + rect.top - root.clientHeight / 4;
  }, [highlights, sizes, width]);

  return (
    <div ref={scroller} onScroll={reportPage} className={cn("relative overflow-y-auto bg-muted", className)}>
      {load.status === "error" ? (
        <Message>{load.message}</Message>
      ) : file == null ? (
        <PageSkeleton />
      ) : (
        <Document
          file={file}
          onLoadSuccess={onLoad}
          loading={<PageSkeleton />}
          error={<Message>Unreadable PDF</Message>}
          className="flex flex-col gap-2 py-2"
        >
          {sizes &&
            width > 0 &&
            sizes.map((size, i) => {
              const n = i + 1;
              return (
                <div
                  key={n}
                  data-page={n}
                  ref={(el) => {
                    if (el) slots.current.set(n, el);
                    else slots.current.delete(n);
                  }}
                  className="relative mx-auto bg-white shadow-sm"
                  style={{ width, height: (width * size.height) / size.width }}
                >
                  {visible.has(n) && (
                    <Page
                      pageNumber={n}
                      width={width}
                      renderTextLayer={false}
                      renderAnnotationLayer={false}
                      loading={null}
                    />
                  )}
                  {highlights
                    ?.filter((r) => r.page === n)
                    .map((r, j) => {
                      const rect = regionToRect(r, size.width, width);
                      return (
                        <div
                          key={j}
                          data-highlight
                          className="pointer-events-none absolute rounded-sm bg-yellow-300/40 ring-1 ring-yellow-500/60"
                          style={rect}
                        />
                      );
                    })}
                </div>
              );
            })}
        </Document>
      )}
    </div>
  );
}

function usePdfBytes(docId: string): Load {
  const [load, setLoad] = useState<Load>({ status: "loading" });
  useEffect(() => {
    const controller = new AbortController();
    setLoad({ status: "loading" });
    (async () => {
      try {
        const response = await fetch(docPdfUrl(docId), { signal: controller.signal });
        if (!response.ok) {
          setLoad({ status: "error", message: ERRORS[response.status] ?? (await detail(response)) });
          return;
        }
        setLoad({ status: "ready", data: new Uint8Array(await response.arrayBuffer()) });
      } catch (error) {
        if (!controller.signal.aborted) setLoad({ status: "error", message: String(error) });
      }
    })();
    return () => controller.abort();
  }, [docId]);
  return load;
}

async function detail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") return body.detail;
  } catch {
    // Not JSON.
  }
  return `Failed to load PDF (${response.status})`;
}

function useWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      // Leave room for the vertical scrollbar and a small margin.
      setWidth(Math.max(0, Math.floor(entry.contentRect.width) - 16));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}

function Message({ children }: { children: ReactNode }) {
  return <div className="flex h-full min-h-40 items-center justify-center text-sm text-muted-foreground">{children}</div>;
}

function PageSkeleton() {
  return (
    <div className="p-2">
      <Skeleton className="aspect-[8.5/11] w-full" />
    </div>
  );
}
