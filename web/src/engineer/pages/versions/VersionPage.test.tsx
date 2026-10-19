import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { VersionDetail, VersionDocOut } from "@/api/types";
import { mockFetch, renderRoute, renderWithProviders } from "@/test/utils";

import { versionDetail } from "./fixtures";
import { VersionPage } from "./VersionPage";

function doc(id: string, extra: Partial<VersionDocOut> = {}): VersionDocOut {
  return {
    doc_id: id,
    status: "ok",
    cached: false,
    filename: `${id}.pdf`,
    path: `x/${id}.pdf`,
    job_number: "2292",
    project: "SZEKERES",
    error_type: null,
    error_message: null,
    duration_s: 0.25,
    peak_rss_mb: 100,
    metrics: { records: 12, chunks: 12, tokens: 2052, tokens_mean: 171 },
    issues: { error: 0, warning: 1 },
    ...extra,
  };
}

function setup(detail: VersionDetail = versionDetail(5)) {
  return mockFetch({
    "GET /api/versions/:id": detail,
    "GET /api/versions/:id/docs": (request: Request) => {
      const status = new URL(request.url).searchParams.get("status");
      const items = status === "error" ? [doc("bad", { status: "error", error_type: "ValueError" })] : [doc("good")];
      return { total: items.length, items };
    },
    "GET /api/versions/:id/issues": [],
    "GET /api/dags/:dag/nodes/:node/versions": [versionDetail(5), versionDetail(2, { label: "default/chunk@v2" })],
    "GET /api/dags/:dag": { name: "default", nodes: [] },
  });
}

const requests = (fetchMock: ReturnType<typeof setup>, path: string) =>
  fetchMock.mock.calls.map(([r]) => new URL((r as Request).url)).filter((u) => u.pathname === path);

describe("VersionPage", () => {
  it("shows the header, lineage and chunk metric cards", async () => {
    setup();
    await renderWithProviders(<VersionPage />, { path: "/versions/5" });
    expect(await screen.findByText("default/chunk@v5")).toBeInTheDocument();
    expect(screen.getByText("Promotable")).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Chunks" })).getByText("148")).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Tokens per chunk" })).getByText("171.7")).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Size" })).getByText("158 KB")).toBeInTheDocument();
    expect(screen.getByText("default/ocr@v1")).toBeInTheDocument();
    expect(screen.getByText("nodes/hybrid_chunk.py")).toBeInTheDocument();
    expect(screen.getByText("docling.HybridChunker")).toBeInTheDocument();
    expect(await screen.findByText("good.pdf")).toBeInTheDocument();
    expect(screen.getByText("12 chunks · 171 tok/chunk")).toBeInTheDocument();
  });

  it("flags changed checks and disables Promote when not promotable", async () => {
    setup(versionDetail(5, { checks_changed: true, promotable: false, not_promotable_reasons: ["scratch"] }));
    await renderWithProviders(<VersionPage />, { path: "/versions/5" });
    expect(await screen.findByText("Checks changed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Promote" })).toBeDisabled();
  });

  it("filters docs from search params and writes filters back", async () => {
    const fetchMock = setup();
    const { router } = await renderWithProviders(<VersionPage />, { path: "/versions/5?status=error" });
    expect(await screen.findByText("bad.pdf")).toBeInTheDocument();
    expect(requests(fetchMock, "/api/versions/5/docs")[0].searchParams.get("status")).toBe("error");

    await userEvent.type(screen.getByRole("textbox", { name: "Find document" }), "22");
    await waitFor(() => expect(router.state.location.search).toEqual({ status: "error", q: "22" }));
    await waitFor(() =>
      expect(requests(fetchMock, "/api/versions/5/docs").some((u) => u.searchParams.get("q") === "22")).toBe(true),
    );
  });

  it("opens a document in the Document view", async () => {
    setup();
    // The whole app, so the version page unmounts on navigation.
    const { router } = await renderRoute("/versions/5");
    await userEvent.click(await screen.findByText("good.pdf"));
    await waitFor(() => expect(router.state.location.pathname).toBe("/dags/default/docs/good"));
    expect(router.state.location.search).toEqual({ node: "chunk", version: 5 });
  });

  it("switches to the issues tab via search params", async () => {
    setup();
    const { router } = await renderWithProviders(<VersionPage />, { path: "/versions/5" });
    await userEvent.click(await screen.findByRole("tab", { name: /Issues/ }));
    await waitFor(() => expect(router.state.location.search).toEqual({ tab: "issues" }));
    expect(await screen.findByText("No issues")).toBeInTheDocument();
  });

  it("compares with a picked version", async () => {
    setup();
    const { router } = await renderRoute("/versions/5");
    await userEvent.click(await screen.findByRole("button", { name: "Compare with…" }));
    await userEvent.click(await screen.findByRole("combobox", { name: "Version" }));
    await userEvent.click(await screen.findByRole("option", { name: "default/chunk@v2" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/compare"));
    expect(router.state.location.search).toEqual({ a: 2, b: 5 });
  });
});
