import { describe, expect, it } from "vitest";

import { hit } from "./fixtures";
import { buildSearchRequest, firstPage, formatPages, rankChanges } from "./results";

describe("buildSearchRequest", () => {
  it("needs a query and a left version", () => {
    expect(buildSearchRequest(undefined, 1, 2, 10)).toBeNull();
    expect(buildSearchRequest("  ", 1, 2, 10)).toBeNull();
    expect(buildSearchRequest("pumps", undefined, 2, 10)).toBeNull();
  });

  it("searches both sides in one request, k defaulting to 10", () => {
    expect(buildSearchRequest("pumps", 1, 2, undefined)).toEqual({ query: "pumps", versions: [1, 2], k: 10 });
    expect(buildSearchRequest("pumps", 1, undefined, 20)).toEqual({ query: "pumps", versions: [1], k: 20 });
  });
});

describe("rankChanges", () => {
  it("marks new, moved and unchanged passages", () => {
    const left = [hit(1, { text_hash: "x" }), hit(2, { text_hash: "y" }), hit(3, { text_hash: "z" })];
    const right = [
      hit(1, { chunk_id: "other", text_hash: "y" }),
      hit(2, { chunk_id: "cx", text_hash: "y", doc_id: "d2" }),
      hit(3, { text_hash: "z" }),
    ];
    expect(rankChanges(right, left)).toEqual([{ kind: "was", rank: 2 }, { kind: "new" }, { kind: "same" }]);
  });

  it("falls back to the chunk id without a text hash", () => {
    const left = [hit(1, { chunk_id: "a", text_hash: null }), hit(2, { chunk_id: "b", text_hash: null })];
    const right = [hit(1, { chunk_id: "b", text_hash: null }), hit(2, { chunk_id: "c", text_hash: "q" })];
    expect(rankChanges(right, left)).toEqual([{ kind: "was", rank: 2 }, { kind: "new" }]);
  });
});

describe("formatPages", () => {
  it("formats single pages, ranges and lists", () => {
    expect(formatPages([])).toBe("");
    expect(formatPages([3])).toBe("p. 3");
    expect(formatPages([5, 3, 4])).toBe("pp. 3–5");
    expect(formatPages([3, 7])).toBe("pp. 3, 7");
  });
});

describe("firstPage", () => {
  it("uses the lowest page, else the first region", () => {
    expect(firstPage({ pages: [4, 2], regions: [] })).toBe(2);
    expect(firstPage({ pages: [], regions: [{ page: 6, x0: 0, y0: 0, x1: 1, y1: 1 }] })).toBe(6);
    expect(firstPage({ pages: [], regions: [] })).toBeUndefined();
  });
});
