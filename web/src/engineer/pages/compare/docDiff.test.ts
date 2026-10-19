import { describe, expect, it } from "vitest";

import type { DocChangeOut } from "@/api/types";

import { docDiffView, sortChanges } from "./docDiff";

const row = (seq: number, text: string, hash: string) => ({
  chunk_id: `c${seq}${hash}`,
  seq,
  text,
  pages: [1, 2],
  section: ["Scope", "Survey"],
  text_hash: hash,
});

describe("docDiffView", () => {
  it("marks table rows whose text_hash is on one side only", () => {
    const view = docDiffView({
      a: [row(0, "same", "h1"), row(1, "old", "h2")],
      b: [row(0, "same", "h1"), row(1, "new", "h3"), row(2, "more", "h4")],
      only_a: ["h2"],
      only_b: ["h3", "h4"],
    });
    expect(view.kind).toBe("table");
    if (view.kind !== "table") return;
    expect(view.a!.map((r) => r.changed)).toEqual([false, true]);
    expect(view.b!.map((r) => r.changed)).toEqual([false, true, true]);
    expect(view.a![0]).toMatchObject({ seq: 0, section: "Scope › Survey", pages: "1, 2", text: "same" });
  });

  it("keeps a missing table side as null", () => {
    const view = docDiffView({ a: null, b: [row(0, "x", "h1")], only_a: [], only_b: ["h1"] });
    expect(view).toMatchObject({ kind: "table", a: null });
  });

  it("compares Docling text elements of parse records", () => {
    const parse = (...texts: string[]) => ({ pages: [], docling: { texts: texts.map((text) => ({ label: "text", text })) } });
    const view = docDiffView({ a: parse("Title", "Old  line", "dup"), b: parse("title", "New line", "dup", "dup"), only_a: null, only_b: null });
    expect(view.kind).toBe("texts");
    if (view.kind !== "texts") return;
    expect(view.a!.map((t) => [t.text, t.changed])).toEqual([
      ["Title", false],
      ["Old  line", true],
      ["dup", false],
    ]);
    expect(view.b!.map((t) => t.changed)).toEqual([false, true, false, true]);
  });

  it("marks everything when the other side is missing", () => {
    const view = docDiffView({ a: { docling: { texts: [{ text: "x" }] } }, b: null, only_a: null, only_b: null });
    expect(view).toMatchObject({ kind: "texts", a: [{ text: "x", changed: true }], b: null });
  });

  it("falls back to JSON for unknown shapes", () => {
    expect(docDiffView({ a: { foo: 1 }, b: { foo: 2 }, only_a: null, only_b: null })).toEqual({
      kind: "json",
      a: { foo: 1 },
      b: { foo: 2 },
    });
  });
});

describe("sortChanges", () => {
  const change = (doc_id: string, change: number): DocChangeOut => ({
    doc_id,
    filename: `${doc_id}.pdf`,
    job_number: null,
    project: null,
    a_records: 1,
    b_records: 1,
    change,
  });

  it("puts the largest change first, ties by filename", () => {
    expect(sortChanges([change("b", 1), change("c", 13), change("a", 1), change("d", 76)]).map((c) => c.doc_id)).toEqual([
      "d",
      "c",
      "a",
      "b",
    ]);
  });
});
