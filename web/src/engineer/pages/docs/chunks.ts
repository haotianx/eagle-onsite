/** Reads chunk and embed rows (contract CHUNK_COLUMNS); regions are already PDF points, top-left origin. */

import type { Region } from "@/components/pdf/regions";

export interface ChunkRow {
  chunk_id: string;
  seq: number;
  text: string;
  pages: number[];
  section: string[];
  tokens: number | undefined;
  regions: Region[];
}

type Obj = Record<string, unknown>;

function isObj(value: unknown): value is Obj {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function num(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function region(value: unknown): Region | null {
  if (!isObj(value)) return null;
  const [page, x0, y0, x1, y1] = [value.page, value.x0, value.y0, value.x1, value.y1].map(num);
  if (page == null || x0 == null || y0 == null || x1 == null || y1 == null) return null;
  return { page, x0, y0, x1, y1 };
}

/** Whether `record` is a list of rows with chunk columns. */
export function isChunkRecord(record: unknown): record is Obj[] {
  return Array.isArray(record) && record.every((row) => isObj(row) && "seq" in row && "text" in row);
}

/** Rows in `seq` order. */
export function chunkRows(rows: Obj[]): ChunkRow[] {
  return rows
    .map((row) => ({
      chunk_id: String(row.chunk_id ?? row.seq),
      seq: num(row.seq) ?? 0,
      text: typeof row.text === "string" ? row.text : "",
      pages: Array.isArray(row.pages) ? row.pages.filter((p): p is number => num(p) != null) : [],
      section: Array.isArray(row.section) ? row.section.map(String) : [],
      tokens: num(row.tokens),
      regions: Array.isArray(row.regions) ? row.regions.map(region).filter((r): r is Region => r != null) : [],
    }))
    .sort((a, b) => a.seq - b.seq);
}

/** The page to scroll to for a chunk: its first region's page, else its first page. */
export function chunkPage(chunk: ChunkRow): number | undefined {
  return chunk.regions[0]?.page ?? chunk.pages[0];
}
