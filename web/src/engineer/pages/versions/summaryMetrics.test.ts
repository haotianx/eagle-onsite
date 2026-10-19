import { describe, expect, it } from "vitest";

import { docMetricsText, formatDelta, summaryMetrics } from "./summaryMetrics";

const labels = (type: string | null, scope: "version" | "compare") => summaryMetrics(type, scope).map((m) => m.label);
const metric = (type: string, key: string) => summaryMetrics(type, "compare").find((m) => m.key === key)!;

describe("summaryMetrics", () => {
  it("adds each artifact type's own metrics to the generic ones", () => {
    expect(labels(null, "version")).toEqual(["Docs", "OK", "Failed", "Skipped", "Records", "Size"]);
    expect(labels("parse", "compare")).toEqual(["Docs", "Records", "Failures", "Size", "Pages", "OCR'd pages", "Tables"]);
    expect(labels("chunk", "compare").slice(4)).toEqual(["Chunks", "Tokens per chunk"]);
    expect(labels("embed", "version").slice(6)).toEqual(["Vectors", "Model"]);
  });

  it("formats values", () => {
    expect(metric("chunk", "tokens_per_chunk").format(171.72297)).toBe("171.7");
    expect(metric("chunk", "records").format(12345)).toBe("12,345");
    expect(metric("chunk", "size_bytes").format(161358)).toBe("158 KB");
    expect(metric("embed", "model").format(null)).toBe("—");
  });
});

describe("formatDelta", () => {
  it("signs numeric differences in the metric's format", () => {
    expect(formatDelta(metric("chunk", "records"), 86, 148)).toBe("+62");
    expect(formatDelta(metric("chunk", "tokens_per_chunk"), 259.35, 171.72)).toBe("−87.6");
    expect(formatDelta(metric("chunk", "size_bytes"), 166769, 161358)).toBe("−5.3 KB");
  });

  it("is null when equal or missing, and 'changed' for differing text", () => {
    expect(formatDelta(metric("chunk", "docs"), 6, 6)).toBeNull();
    expect(formatDelta(metric("chunk", "docs"), undefined, 6)).toBeNull();
    expect(formatDelta(metric("embed", "model"), "a", "b")).toBe("changed");
    expect(formatDelta(metric("embed", "model"), "a", "a")).toBeNull();
  });
});

describe("docMetricsText", () => {
  it("summarizes per-doc metrics by type", () => {
    expect(docMetricsText("parse", { records: 1, pages: 2, ocr_pages: 0, tables: 2 })).toBe("2 pages · 0 OCR'd · 2 tables");
    expect(docMetricsText("chunk", { records: 43, chunks: 43, tokens: 8364, tokens_mean: 194.5116 })).toBe(
      "43 chunks · 194.5 tok/chunk",
    );
    expect(docMetricsText("embed", { records: 12, vectors: 12 })).toBe("12 vectors");
    expect(docMetricsText(null, {})).toBe("—");
  });
});
