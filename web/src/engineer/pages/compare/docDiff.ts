import type { DocChangeOut, DocDiffOut } from "@/api/types";

export interface ChunkRow {
  key: string;
  seq: unknown;
  section: string;
  pages: string;
  text: string;
  changed: boolean;
}

export interface TextElement {
  key: string;
  label: string | null;
  text: string;
  changed: boolean;
}

export type DocDiffView =
  | { kind: "table"; a: ChunkRow[] | null; b: ChunkRow[] | null }
  | { kind: "texts"; a: TextElement[] | null; b: TextElement[] | null }
  | { kind: "json"; a: unknown; b: unknown };

type Row = Record<string, unknown>;

function joined(value: unknown, separator: string): string {
  return Array.isArray(value) ? value.map(String).join(separator) : value == null ? "" : String(value);
}

function tableRows(record: unknown, changedHashes: Set<string>): ChunkRow[] | null {
  if (!Array.isArray(record)) return null;
  return (record as Row[]).map((row, i) => ({
    key: String(row.chunk_id ?? i),
    seq: row.seq ?? i,
    section: joined(row.section, " › "),
    pages: joined(row.pages, ", "),
    text: row.text == null ? "" : String(row.text),
    changed: typeof row.text_hash === "string" && changedHashes.has(row.text_hash),
  }));
}

/** Docling `texts[]` of a parse record in order, or null when the record has none. */
export function doclingTexts(record: unknown): { label: string | null; text: string }[] | null {
  if (!record || typeof record !== "object" || Array.isArray(record)) return null;
  const docling = (record as Row).docling;
  if (!docling || typeof docling !== "object") return null;
  const texts = (docling as Row).texts;
  if (!Array.isArray(texts)) return null;
  return texts
    .filter((t): t is Row => !!t && typeof t === "object")
    .map((t) => ({ label: typeof t.label === "string" ? t.label : null, text: t.text == null ? "" : String(t.text) }));
}

function normalize(text: string): string {
  return text.toLowerCase().replace(/\s+/g, " ").trim();
}

/** Marks elements whose (normalized) text doesn't occur as often on the other side. */
function markTexts(
  side: { label: string | null; text: string }[],
  other: { label: string | null; text: string }[] | null,
): TextElement[] {
  const remaining = new Map<string, number>();
  for (const t of other ?? []) {
    const key = normalize(t.text);
    remaining.set(key, (remaining.get(key) ?? 0) + 1);
  }
  return side.map((t, i) => {
    const key = normalize(t.text);
    const left = remaining.get(key) ?? 0;
    if (left > 0) remaining.set(key, left - 1);
    return { key: String(i), label: t.label, text: t.text, changed: left === 0 };
  });
}

/** How to show one doc's two records side by side: chunk/embed tables (rows whose text_hash is on
 * one side only are changed), parse JSON (Docling text elements, differing ones changed), or raw JSON. */
export function docDiffView(diff: DocDiffOut): DocDiffView {
  if (Array.isArray(diff.a) || Array.isArray(diff.b)) {
    return {
      kind: "table",
      a: tableRows(diff.a, new Set(diff.only_a ?? [])),
      b: tableRows(diff.b, new Set(diff.only_b ?? [])),
    };
  }
  const ta = doclingTexts(diff.a);
  const tb = doclingTexts(diff.b);
  if ((ta || diff.a == null) && (tb || diff.b == null) && (ta || tb)) {
    return { kind: "texts", a: ta && markTexts(ta, tb), b: tb && markTexts(tb, ta) };
  }
  return { kind: "json", a: diff.a, b: diff.b };
}

/** Changed docs, largest change first (ties by filename). */
export function sortChanges(changed: DocChangeOut[]): DocChangeOut[] {
  return [...changed].sort(
    (x, y) => y.change - x.change || (x.filename ?? x.doc_id).localeCompare(y.filename ?? y.doc_id),
  );
}
