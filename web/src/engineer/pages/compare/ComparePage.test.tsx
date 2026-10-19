import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { ComparisonOut, DocChangeOut } from "@/api/types";
import { mockFetch, renderWithProviders } from "@/test/utils";

import { versionDetail, versionSummary } from "../versions/fixtures";
import { ComparePage, recordsText } from "./ComparePage";

const v2 = versionSummary(2, { current: true });
const v5 = versionSummary(5);

function change(doc_id: string, a: number | null, b: number | null, n: number): DocChangeOut {
  return { doc_id, filename: `${doc_id}.pdf`, job_number: null, project: null, a_records: a, b_records: b, change: n };
}

const comparison: ComparisonOut = {
  a: v2,
  b: v5,
  a_summary: { docs: 6, failed: 0, size_bytes: 166769, records: 86, chunks: 86, tokens_per_chunk: 259.35 },
  b_summary: { docs: 6, failed: 0, size_bytes: 161358, records: 148, chunks: 148, tokens_per_chunk: 171.72 },
  changed: [change("small", 7, 12, 13), change("gone", 3, null, 3), change("big", 39, 69, 76)],
  unchanged: 2,
};

const row = (seq: number, text: string, hash: string) => ({ seq, text, pages: [1], section: [], text_hash: hash });

function setup() {
  return mockFetch({
    "GET /api/versions/:id": (_r: Request, { id }: Record<string, string>) => versionDetail(Number(id)),
    "GET /api/dags/:dag/nodes/:node/versions": [v5, v2],
    "GET /api/dags/:dag": {
      name: "default",
      nodes: [{ name: "chunk", current: v2, candidates: [versionSummary(4), v5], scratch: [] }],
    },
    "GET /api/compare": comparison,
    "GET /api/compare/docs/:doc": {
      a: [row(0, "Same text", "h1"), row(1, "Old text", "h2")],
      b: [row(0, "Same text", "h1"), row(1, "New text", "h3")],
      only_a: ["h2"],
      only_b: ["h3"],
    },
  });
}

describe("ComparePage", () => {
  it("defaults b to the newest candidate", async () => {
    setup();
    const { router } = await renderWithProviders(<ComparePage />, { path: "/compare?a=2" });
    await waitFor(() => expect(router.state.location.search).toEqual({ a: 2, b: 5 }));
  });

  it("defaults a to the current version", async () => {
    setup();
    const { router } = await renderWithProviders(<ComparePage />, { path: "/compare?b=5" });
    await waitFor(() => expect(router.state.location.search).toEqual({ a: 2, b: 5 }));
  });

  it("shows metric deltas and changed docs sorted by change", async () => {
    setup();
    await renderWithProviders(<ComparePage />, { path: "/compare?a=2&b=5" });
    const records = await screen.findByRole("group", { name: "Records" });
    expect(within(records).getByText("148")).toBeInTheDocument();
    expect(within(records).getByText("A 86")).toBeInTheDocument();
    expect(within(records).getByText("+62")).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Tokens per chunk" })).getByText("−87.6")).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Docs" })).queryByText(/^[+−]/)).not.toBeInTheDocument();

    const rows = screen.getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell")[0].textContent)).toEqual(["big.pdf", "small.pdf", "gone.pdf"]);
    expect(within(rows[0]).getByText("39 → 69")).toBeInTheDocument();
    expect(within(rows[2]).getByText("only in a")).toBeInTheDocument();
    expect(screen.getByText("3 changed · 2 unchanged")).toBeInTheDocument();
  });

  it("selects a doc into ?doc= and marks changed boundaries side by side", async () => {
    setup();
    const { router } = await renderWithProviders(<ComparePage />, { path: "/compare?a=2&b=5" });
    await userEvent.click(await screen.findByText("small.pdf"));
    await waitFor(() => expect(router.state.location.search).toEqual({ a: 2, b: 5, doc: "small" }));
    const old = (await screen.findByText("Old text")).closest("li")!;
    expect(old).toHaveAttribute("data-changed", "true");
    expect(screen.getByText("New text").closest("li")).toHaveAttribute("data-changed", "true");
    for (const same of screen.getAllByText("Same text")) expect(same.closest("li")).not.toHaveAttribute("data-changed");
  });

  it("formats records for docs on one side", () => {
    expect(recordsText({ a_records: null, b_records: 4 })).toBe("only in b");
    expect(recordsText({ a_records: 4, b_records: null })).toBe("only in a");
    expect(recordsText({ a_records: 1200, b_records: 4 })).toBe("1,200 → 4");
  });
});
