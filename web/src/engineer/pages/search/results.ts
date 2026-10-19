import type { SearchHit, SearchIn } from "@/api/types";

export const DEFAULT_K = 10;
export const K_OPTIONS = [5, 10, 20, 50] as const;

/** The request for a search, or null until there is a query and a left version. */
export function buildSearchRequest(
  query: string | undefined,
  a: number | undefined,
  b: number | undefined,
  k: number | undefined,
): SearchIn | null {
  if (!query?.trim() || a == null) return null;
  return { query, versions: b == null ? [a] : [a, b], k: k ?? DEFAULT_K };
}

export type RankChange = { kind: "same" } | { kind: "new" } | { kind: "was"; rank: number };

type Passage = Pick<SearchHit, "rank" | "doc_id" | "text_hash" | "chunk_id">;

/** Each hit's rank change relative to `other`: the same passage matched by doc and text hash,
 * falling back to chunk id. */
export function rankChanges(hits: Passage[], other: Passage[]): RankChange[] {
  const byText = new Map<string, number>();
  const byChunk = new Map<string, number>();
  for (const hit of other) {
    if (hit.text_hash) setFirst(byText, `${hit.doc_id}\0${hit.text_hash}`, hit.rank);
    setFirst(byChunk, hit.chunk_id, hit.rank);
  }
  return hits.map((hit) => {
    const rank =
      (hit.text_hash ? byText.get(`${hit.doc_id}\0${hit.text_hash}`) : undefined) ?? byChunk.get(hit.chunk_id);
    if (rank == null) return { kind: "new" };
    return rank === hit.rank ? { kind: "same" } : { kind: "was", rank };
  });
}

function setFirst(map: Map<string, number>, key: string, value: number) {
  if (!map.has(key)) map.set(key, value);
}

/** [3] → "p. 3", [3, 4, 5] → "pp. 3–5", [3, 7] → "pp. 3, 7". */
export function formatPages(pages: number[]): string {
  if (pages.length === 0) return "";
  const sorted = [...new Set(pages)].sort((x, y) => x - y);
  if (sorted.length === 1) return `p. ${sorted[0]}`;
  const contiguous = sorted.every((p, i) => i === 0 || p === sorted[i - 1] + 1);
  return contiguous ? `pp. ${sorted[0]}–${sorted.at(-1)}` : `pp. ${sorted.join(", ")}`;
}

/** The page a hit opens at: its first page, else its first region's page. */
export function firstPage(hit: Pick<SearchHit, "pages" | "regions">): number | undefined {
  if (hit.pages.length > 0) return Math.min(...hit.pages);
  return hit.regions[0]?.page;
}
