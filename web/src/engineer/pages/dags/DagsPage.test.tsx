import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { DagSummary } from "@/api/types";
import { json, mockFetch, renderWithProviders } from "@/test/utils";

import { DagsPage, stateCounts } from "./DagsPage";

const dags: DagSummary[] = [
  {
    name: "default",
    source: "proposals",
    path: "dags/default.yaml",
    dirty: true,
    errors: [],
    node_count: 3,
    states: { fresh: 1, candidate: 2 },
    last_run: {
      id: 5,
      dag: "default",
      kind: "dag",
      status: "cancelled",
      scratch: false,
      commit: "abc",
      dirty: false,
      created_at: new Date(Date.now() - 5 * 60_000).toISOString(),
      started_at: null,
      ended_at: null,
      duration_s: null,
      nodes: ["ocr"],
      processed: 2,
      cached: 0,
      failed: 0,
      error: null,
      cancel_requested: true,
      doc_filename: null,
    },
  },
  {
    name: "broken",
    source: null,
    path: "dags/broken.yaml",
    dirty: false,
    errors: ["Unknown input 'x' for node 'chunk'"],
    node_count: 0,
    states: {},
    last_run: null,
  },
];

describe("DagsPage", () => {
  it("lists DAGs with source, last run, state counts, dirty badge and errors", async () => {
    mockFetch({ "GET /api/dags": dags });
    await renderWithProviders(<DagsPage />);
    const rows = await screen.findAllByRole("row");
    const first = within(rows[1]);
    expect(first.getByRole("link", { name: "default" })).toHaveAttribute("href", "/dags/default");
    expect(first.getByText("Uncommitted")).toBeInTheDocument();
    expect(first.getByText("proposals")).toBeInTheDocument();
    const lastRun = first.getByRole("link", { name: /Cancelled/ });
    expect(lastRun).toHaveAttribute("href", "/runs/5");
    expect(lastRun).toHaveTextContent("5 minutes ago");
    expect(rows[1]).toHaveTextContent("Candidate2Fresh1");

    const second = within(rows[2]);
    expect(second.getByText("Unknown input 'x' for node 'chunk'")).toBeInTheDocument();
    expect(second.queryByText("Uncommitted")).not.toBeInTheDocument();
  });

  it("opens a DAG from its row", async () => {
    mockFetch({ "GET /api/dags": dags });
    const { router } = await renderWithProviders(<DagsPage />);
    await userEvent.click(await screen.findByText("proposals"));
    expect(router.state.location.pathname).toBe("/dags/default");
  });

  it("shows the API error", async () => {
    mockFetch({ "GET /api/dags": () => json({ detail: "Repo not found" }, 500) });
    await renderWithProviders(<DagsPage />);
    expect(await screen.findByText("Repo not found")).toBeInTheDocument();
  });
});

describe("stateCounts", () => {
  it("orders non-zero counts", () => {
    expect(stateCounts({ fresh: 1, failed: 2, stale: 0 })).toEqual([
      ["failed", 2],
      ["fresh", 1],
    ]);
  });
});
