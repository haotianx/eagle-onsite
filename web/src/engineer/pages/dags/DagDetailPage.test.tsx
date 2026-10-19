import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { json, mockFetch, renderWithProviders } from "@/test/utils";

import { DagDetailPage, isRunning } from "./DagDetailPage";
import { dagDetail, embed } from "./fixtures";

function mockApi() {
  return mockFetch({
    "GET /api/dags/:dag": dagDetail,
    "GET /api/sources": [],
    "POST /api/runs/plan": () => json({ detail: "No snapshot" }, 400),
  });
}

function graphNode(name: string): HTMLElement {
  const el = document.querySelector<HTMLElement>(`.react-flow__node[data-id="${name}"]`);
  if (!el) throw new Error(`No graph node ${name}`);
  return el;
}

describe("DagDetailPage", () => {
  it("shows the DAG header and one graph node per DAG node", async () => {
    mockApi();
    await renderWithProviders(<DagDetailPage />, { path: "/dags/default" });
    expect(await screen.findByRole("heading", { name: "default" })).toBeInTheDocument();
    expect(screen.getByText("proposals")).toBeInTheDocument();
    expect(screen.getByText("Uncommitted")).toBeInTheDocument();
    await waitFor(() => expect(document.querySelectorAll(".react-flow__node")).toHaveLength(3));
    const chunk = within(graphNode("chunk"));
    expect(chunk.getByText("Candidate")).toBeInTheDocument();
    expect(chunk.getByText("default/chunk@v1")).toBeInTheDocument();
    expect(within(graphNode("embed")).getByText("Never run")).toBeInTheDocument();
  });

  it("selects a node on click and clears the selection on the background", async () => {
    mockApi();
    const { router } = await renderWithProviders(<DagDetailPage />, { path: "/dags/default" });
    await screen.findByRole("heading", { name: "default" });
    await waitFor(() => graphNode("parse"));
    // d3-zoom's mousedown handler needs a real window, so click without pointer events.
    fireEvent.click(graphNode("parse"));
    await waitFor(() => expect(router.state.location.search).toEqual({ node: "parse" }));
    expect(await screen.findByRole("complementary", { name: "Node parse" })).toBeInTheDocument();

    fireEvent.click(document.querySelector(".react-flow__pane")!);
    await waitFor(() => expect(router.state.location.search).toEqual({}));
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  });

  it("shows the selected node's details and action states", async () => {
    mockApi();
    await renderWithProviders(<DagDetailPage />, { path: "/dags/default?node=parse" });
    const panel = within(await screen.findByRole("complementary", { name: "Node parse" }));
    expect(panel.getByText("nodes/parse.py")).toBeInTheDocument();
    expect(panel.getByText("checks/parse.py")).toBeInTheDocument();
    expect(panel.getByText("ocr")).toBeInTheDocument();
    expect(panel.getByText("true")).toBeInTheDocument();
    expect(panel.getByText("docling")).toBeInTheDocument();
    expect(panel.getByText("6")).toBeInTheDocument();
    expect(panel.getByText("1")).toBeInTheDocument();
    expect(panel.getByRole("button", { name: "Promote" })).toBeDisabled();
    expect(panel.getByRole("link", { name: "Open artifact" })).toHaveAttribute("href", "/versions/1");
  });

  it("links every version of the selected node and enables Promote with a candidate", async () => {
    mockApi();
    await renderWithProviders(<DagDetailPage />, { path: "/dags/default?node=chunk" });
    const panel = within(await screen.findByRole("complementary", { name: "Node chunk" }));
    expect(panel.getByText("candidate available")).toBeInTheDocument();
    const hrefs = panel.getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(hrefs).toEqual(["/versions/2", "/versions/2", "/versions/3", "/versions/5", "/versions/4"]);
    expect(panel.getByRole("button", { name: "Promote" })).toBeEnabled();
  });

  it("disables Open artifact for a node without a current version and opens the launcher", async () => {
    mockApi();
    await renderWithProviders(<DagDetailPage />, { path: "/dags/default?node=embed" });
    const panel = within(await screen.findByRole("complementary", { name: "Node embed" }));
    expect(panel.getByRole("button", { name: "Open artifact" })).toBeDisabled();
    await userEvent.click(panel.getByRole("button", { name: "Run…" }));
    expect(await screen.findByRole("dialog", { name: "Run default" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "node" })).toHaveAttribute("aria-checked", "true");
    expect(await screen.findByText("No snapshot")).toBeInTheDocument();
  });

  it("shows the API error", async () => {
    mockFetch({ "GET /api/dags/:dag": () => json({ detail: "DAG 'x' not found" }, 404) });
    await renderWithProviders(<DagDetailPage />, { path: "/dags/x" });
    expect(await screen.findByText("DAG 'x' not found")).toBeInTheDocument();
  });
});

describe("isRunning", () => {
  it("is true while any node is running", () => {
    expect(isRunning(dagDetail)).toBe(false);
    expect(isRunning({ ...dagDetail, nodes: [{ ...embed, state: "running" }] })).toBe(true);
    expect(isRunning({ ...dagDetail, nodes: [{ ...embed, active_run_id: 4 }] })).toBe(true);
    expect(isRunning(undefined)).toBe(false);
  });
});
