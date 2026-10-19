import { describe, expect, it } from "vitest";

import { chunkPage, chunkRows, isChunkRecord } from "./chunks";
import { bboxToRegion, isParseRecord, parseRecord } from "./docling";

const prov = (page_no: number, l: number, t: number, r: number, b: number, coord_origin = "BOTTOMLEFT") => ({
  page_no,
  bbox: { l, t, r, b, coord_origin },
  charspan: [0, 1],
});

/** A minimal Docling export_to_dict() shape. */
const docling = {
  schema_name: "DoclingDocument",
  body: {
    self_ref: "#/body",
    children: [{ $ref: "#/texts/0" }, { $ref: "#/groups/0" }, { $ref: "#/tables/0" }, { $ref: "#/pictures/0" }],
  },
  groups: [{ self_ref: "#/groups/0", label: "list", children: [{ $ref: "#/texts/1" }, { $ref: "#/texts/2" }] }],
  texts: [
    { self_ref: "#/texts/0", label: "section_header", text: "Scope", prov: [prov(1, 72, 720, 300, 700)], children: [] },
    { self_ref: "#/texts/1", label: "list_item", text: "Pumps", prov: [prov(1, 72, 600, 200, 590)], children: [] },
    { self_ref: "#/texts/2", label: "list_item", text: "Valves", prov: [prov(2, 10, 20, 30, 40, "TOPLEFT")], children: [] },
    { self_ref: "#/texts/3", label: "caption", text: "Figure 1", prov: [prov(2, 0, 100, 50, 90)], children: [] },
  ],
  tables: [
    {
      self_ref: "#/tables/0",
      label: "table",
      prov: [prov(2, 50, 500, 550, 300)],
      children: [],
      data: { grid: [[{ text: "Item" }, { text: "Qty" }], [{ text: "Pump" }, { text: "2" }]] },
    },
  ],
  pictures: [{ self_ref: "#/pictures/0", label: "picture", prov: [prov(2, 0, 200, 100, 100)], children: [{ $ref: "#/texts/3" }] }],
  pages: { "1": { page_no: 1, size: { width: 612, height: 792 } }, "2": { page_no: 2, size: { width: 612, height: 1008 } } },
};

const pages = [
  { page: 1, width: 612, height: 792, text_chars: 120, ocr: false },
  { page: 2, width: 612, height: 1008, text_chars: 0, ocr: true },
];

describe("bboxToRegion", () => {
  it("flips bottom-left bboxes to a top-left origin", () => {
    expect(bboxToRegion(1, { l: 72, t: 720, r: 300, b: 700, coord_origin: "BOTTOMLEFT" }, 792)).toEqual({
      page: 1,
      x0: 72,
      y0: 72,
      x1: 300,
      y1: 92,
    });
  });

  it("keeps top-left bboxes", () => {
    expect(bboxToRegion(3, { l: 1, t: 2, r: 3, b: 4, coord_origin: "TOPLEFT" }, 792)).toEqual({
      page: 3,
      x0: 1,
      y0: 2,
      x1: 3,
      y1: 4,
    });
  });

  it("drops bottom-left bboxes without a page height", () => {
    expect(bboxToRegion(1, { l: 1, t: 2, r: 3, b: 4, coord_origin: "BOTTOMLEFT" }, undefined)).toBeNull();
  });
});

describe("parseRecord", () => {
  it("lists elements in reading order with top-left regions, walking groups and children", () => {
    const record = { docling, pages };
    expect(isParseRecord(record)).toBe(true);
    const { elements, pages: parsed } = parseRecord(record);
    expect(parsed).toEqual(pages);
    expect(elements.map((e) => [e.label, e.text, e.page])).toEqual([
      ["section_header", "Scope", 1],
      ["list_item", "Pumps", 1],
      ["list_item", "Valves", 2],
      ["table", "Item | Qty\nPump | 2", 2],
      ["picture", "", 2],
      ["caption", "Figure 1", 2],
    ]);
    expect(elements[0].regions).toEqual([{ page: 1, x0: 72, y0: 72, x1: 300, y1: 92 }]);
    expect(elements[2].regions).toEqual([{ page: 2, x0: 10, y0: 20, x1: 30, y1: 40 }]);
    // Page 2 is taller: 1008 - 500 = 508.
    expect(elements[3].regions).toEqual([{ page: 2, x0: 50, y0: 508, x1: 550, y1: 708 }]);
    expect(elements[3].cells).toEqual([
      ["Item", "Qty"],
      ["Pump", "2"],
    ]);
  });

  it("falls back to the record's page heights", () => {
    const { pages: _ignored, ...withoutPages } = docling;
    const { elements } = parseRecord({ docling: withoutPages, pages });
    expect(elements[0].regions[0]).toMatchObject({ y0: 72, y1: 92 });
  });
});

describe("chunkRows", () => {
  const rows = [
    {
      chunk_id: "b",
      seq: 1,
      text: "second",
      pages: [2, 3],
      section: ["A", "B"],
      tokens: 40,
      regions: [
        { page: 2, x0: 1, y0: 2, x1: 3, y1: 4 },
        { page: 3, x0: 5, y0: 6, x1: 7, y1: 8 },
      ],
    },
    { chunk_id: "a", seq: 0, text: "first", pages: [1], section: [], tokens: 10, regions: [] },
  ];

  it("orders rows by seq and keeps every region of a chunk", () => {
    expect(isChunkRecord(rows)).toBe(true);
    const chunks = chunkRows(rows);
    expect(chunks.map((c) => c.chunk_id)).toEqual(["a", "b"]);
    expect(chunks[1].regions).toEqual(rows[0].regions);
    expect(chunkPage(chunks[1])).toBe(2);
    expect(chunkPage(chunks[0])).toBe(1);
  });

  it("rejects non-chunk records", () => {
    expect(isChunkRecord({ docling: {} })).toBe(false);
    expect(isChunkRecord([{ foo: 1 }])).toBe(false);
  });
});
