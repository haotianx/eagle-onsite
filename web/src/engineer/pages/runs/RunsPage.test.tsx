import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { mockFetch, renderWithProviders } from "@/test/utils";

import { runSummary } from "./fixtures";
import { RunsPage } from "./RunsPage";

const runs = [
  runSummary({ id: 9, status: "running", nodes: ["chunk"] }),
  runSummary({ id: 8, kind: "single_doc", doc_filename: "P-1234 Bridge.pdf", scratch: true }),
  runSummary({ id: 7, status: "queued", cancel_requested: true }),
  runSummary({ id: 6, status: "failed" }),
];

describe("RunsPage", () => {
  it("renders runs with badges and Cancel only for active runs", async () => {
    mockFetch({ "GET /api/runs": runs, "GET /api/dags": [] });
    await renderWithProviders(<RunsPage />, { path: "/runs" });

    const rows = (await screen.findAllByRole("row")).slice(1);
    expect(rows).toHaveLength(4);
    expect(within(rows[0]).getByRole("link", { name: "#9" })).toHaveAttribute("href", "/runs/9");
    expect(within(rows[0]).getByText("Running")).toBeInTheDocument();
    expect(within(rows[0]).getByRole("button", { name: "Cancel" })).toBeEnabled();

    expect(within(rows[1]).getByText("Single-doc")).toBeInTheDocument();
    expect(within(rows[1]).getByText("P-1234 Bridge.pdf")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Scratch")).toBeInTheDocument();
    expect(within(rows[1]).queryByRole("button")).toBeNull();

    expect(within(rows[2]).getByRole("button", { name: "Cancelling" })).toBeDisabled();
    expect(within(rows[3]).queryByRole("button")).toBeNull();
  });

  it("cancels a run", async () => {
    const fetchMock = mockFetch({
      "GET /api/runs": runs,
      "GET /api/dags": [],
      "POST /api/runs/:id/cancel": () => runSummary({ id: 9, status: "running", cancel_requested: true }),
    });
    await renderWithProviders(<RunsPage />, { path: "/runs" });
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([r]) => r instanceof Request && r.method === "POST" && r.url.endsWith("/api/runs/9/cancel")),
      ).toBe(true),
    );
  });

  it("filters by DAG through the search params", async () => {
    const fetchMock = mockFetch({
      "GET /api/runs": runs,
      "GET /api/dags": [{ name: "default" }, { name: "other" }],
    });
    const { router } = await renderWithProviders(<RunsPage />, { path: "/runs" });
    await userEvent.click(await screen.findByRole("combobox", { name: "DAG" }));
    await userEvent.click(await screen.findByRole("option", { name: "other" }));
    await waitFor(() => expect(router.state.location.search).toEqual({ dag: "other" }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([r]) => r instanceof Request && r.url.includes("/api/runs?dag=other"))).toBe(
        true,
      ),
    );
  });
});
