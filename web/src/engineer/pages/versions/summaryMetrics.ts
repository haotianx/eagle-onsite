import { formatBytes } from "@/lib/format";

/** One entry of a version summary (metrics.version_summary). */
export interface SummaryMetric {
  key: string;
  label: string;
  /** Whether values are numbers (and so have numeric deltas). */
  numeric: boolean;
  format(value: unknown): string;
}

const NONE = "—";

export function formatCount(value: unknown, digits = 0): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return NONE;
  return value.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function count(key: string, label: string, digits = 0): SummaryMetric {
  return { key, label, numeric: true, format: (v) => formatCount(v, digits) };
}

const SIZE: SummaryMetric = {
  key: "size_bytes",
  label: "Size",
  numeric: true,
  format: (v) => (typeof v === "number" ? formatBytes(v) : NONE),
};

const TEXT = (key: string, label: string): SummaryMetric => ({
  key,
  label,
  numeric: false,
  format: (v) => (v == null || v === "" ? NONE : String(v)),
});

const VERSION_GENERIC = [
  count("docs", "Docs"),
  count("ok", "OK"),
  count("failed", "Failed"),
  count("skipped", "Skipped"),
  count("records", "Records"),
  SIZE,
];

const COMPARE_GENERIC = [count("docs", "Docs"), count("records", "Records"), count("failed", "Failures"), SIZE];

const BY_TYPE: Record<string, SummaryMetric[]> = {
  parse: [count("pages", "Pages"), count("ocr_pages", "OCR'd pages"), count("tables", "Tables")],
  chunk: [count("chunks", "Chunks"), count("tokens_per_chunk", "Tokens per chunk", 1)],
  embed: [count("vectors", "Vectors"), TEXT("model", "Model")],
};

/** The summary metrics shown for an artifact type: generic counts, then the type's own. */
export function summaryMetrics(artifactType: string | null | undefined, scope: "version" | "compare"): SummaryMetric[] {
  const generic = scope === "version" ? VERSION_GENERIC : COMPARE_GENERIC;
  return [...generic, ...(BY_TYPE[artifactType ?? ""] ?? [])];
}

/** b relative to a: "+12", "−3", "+1.5 KB"; "changed" for differing text; null when equal or unknown. */
export function formatDelta(metric: SummaryMetric, a: unknown, b: unknown): string | null {
  if (!metric.numeric) return a !== b && a != null && b != null ? "changed" : null;
  if (typeof a !== "number" || typeof b !== "number") return null;
  const delta = b - a;
  if (Math.abs(delta) < 1e-9) return null;
  const magnitude = metric.format(Math.abs(delta));
  return `${delta > 0 ? "+" : "−"}${magnitude}`;
}

/** Per-doc metrics (metrics.doc_metrics) as a short line, e.g. "12 pages · 3 OCR'd · 2 tables". */
export function docMetricsText(artifactType: string | null | undefined, metrics: Record<string, number>): string {
  const parts: [number | undefined, string][] =
    artifactType === "parse"
      ? [
          [metrics.pages, "pages"],
          [metrics.ocr_pages, "OCR'd"],
          [metrics.tables, "tables"],
        ]
      : artifactType === "chunk"
        ? [
            [metrics.chunks, "chunks"],
            [metrics.tokens_mean, "tok/chunk"],
          ]
        : artifactType === "embed"
          ? [[metrics.vectors, "vectors"]]
          : [[metrics.records, "records"]];
  const text = parts
    .filter(([value]) => value != null)
    .map(([value, unit]) => `${formatCount(value, Number.isInteger(value) ? 0 : 1)} ${unit}`)
    .join(" · ");
  return text || NONE;
}
