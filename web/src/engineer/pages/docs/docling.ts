/** Reads a parse record ({docling: DoclingDocument dict, pages}) into pages and elements in reading order. */

import type { Region } from "@/components/pdf/regions";

export interface DoclingBBox {
  l: number;
  t: number;
  r: number;
  b: number;
  coord_origin?: string;
}

export interface ParsePage {
  page: number;
  width: number;
  height: number;
  text_chars: number;
  ocr: boolean;
}

export interface ParseElement {
  ref: string;
  label: string;
  text: string;
  /** First page the element is on, if any. */
  page: number | undefined;
  regions: Region[];
  /** Table cell text by row, for tables. */
  cells?: string[][];
}

export interface ParsedRecord {
  pages: ParsePage[];
  elements: ParseElement[];
}

type Obj = Record<string, unknown>;

function isObj(value: unknown): value is Obj {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function num(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

/** Converts a Docling bbox to a top-left-origin region; null when a bottom-left bbox has no page height. */
export function bboxToRegion(page: number, bbox: DoclingBBox, pageHeight: number | undefined): Region | null {
  const origin = (bbox.coord_origin ?? "TOPLEFT").toUpperCase();
  if (origin === "BOTTOMLEFT") {
    if (pageHeight == null) return null;
    return { page, x0: bbox.l, y0: pageHeight - bbox.t, x1: bbox.r, y1: pageHeight - bbox.b };
  }
  return { page, x0: bbox.l, y0: bbox.t, x1: bbox.r, y1: bbox.b };
}

/** Whether `record` looks like a parse record. */
export function isParseRecord(record: unknown): record is { docling: Obj; pages?: unknown } {
  return isObj(record) && isObj(record.docling);
}

function readPages(record: Obj): ParsePage[] {
  if (!Array.isArray(record.pages)) return [];
  return record.pages.filter(isObj).map((p) => ({
    page: num(p.page) ?? 0,
    width: num(p.width) ?? 0,
    height: num(p.height) ?? 0,
    text_chars: num(p.text_chars) ?? 0,
    ocr: p.ocr === true,
  }));
}

/** Page heights in points: Docling's pages[n].size, else the record's pages[]. */
function pageHeights(docling: Obj, pages: ParsePage[]): Map<number, number> {
  const heights = new Map<number, number>();
  for (const p of pages) if (p.height > 0) heights.set(p.page, p.height);
  if (isObj(docling.pages)) {
    for (const [key, value] of Object.entries(docling.pages)) {
      const height = isObj(value) && isObj(value.size) ? num(value.size.height) : undefined;
      const n = isObj(value) ? (num(value.page_no) ?? Number(key)) : Number(key);
      if (height != null && Number.isInteger(n)) heights.set(n, height);
    }
  }
  return heights;
}

function resolve(docling: Obj, ref: string): Obj | undefined {
  const parts = ref.replace(/^#\//, "").split("/");
  let node: unknown = docling;
  for (const part of parts) {
    if (Array.isArray(node)) node = node[Number(part)];
    else if (isObj(node)) node = node[part];
    else return undefined;
  }
  return isObj(node) ? node : undefined;
}

function tableCells(item: Obj): string[][] | undefined {
  const data = isObj(item.data) ? item.data : undefined;
  if (!data || !Array.isArray(data.grid)) return undefined;
  return data.grid.map((row) => (Array.isArray(row) ? row.map((c) => (isObj(c) && typeof c.text === "string" ? c.text : "")) : []));
}

function element(item: Obj, ref: string, heights: Map<number, number>): ParseElement {
  const regions: Region[] = [];
  let page: number | undefined;
  for (const prov of Array.isArray(item.prov) ? item.prov : []) {
    if (!isObj(prov)) continue;
    const pageNo = num(prov.page_no);
    if (pageNo == null) continue;
    page ??= pageNo;
    if (!isObj(prov.bbox)) continue;
    const region = bboxToRegion(pageNo, prov.bbox as unknown as DoclingBBox, heights.get(pageNo));
    if (region) regions.push(region);
  }
  const cells = ref.startsWith("#/tables/") ? tableCells(item) : undefined;
  const text =
    typeof item.text === "string" ? item.text : cells ? cells.map((row) => row.join(" | ")).join("\n") : "";
  return { ref, label: typeof item.label === "string" ? item.label : "", text, page, regions, ...(cells && { cells }) };
}

/** Elements in reading order: a depth-first walk of the body tree; groups are walked, not listed. */
export function parseRecord(record: { docling: Obj; pages?: unknown }): ParsedRecord {
  const { docling } = record;
  const pages = readPages(record as Obj);
  const heights = pageHeights(docling, pages);
  const elements: ParseElement[] = [];
  const seen = new Set<string>();

  const walk = (node: Obj) => {
    for (const child of Array.isArray(node.children) ? node.children : []) {
      const ref = isObj(child) && typeof child.$ref === "string" ? child.$ref : undefined;
      if (!ref || seen.has(ref)) continue;
      seen.add(ref);
      const item = resolve(docling, ref);
      if (!item) continue;
      if (!ref.startsWith("#/groups/")) elements.push(element(item, ref, heights));
      walk(item);
    }
  };
  if (isObj(docling.body)) walk(docling.body);
  return { pages, elements };
}
