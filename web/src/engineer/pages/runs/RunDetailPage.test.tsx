import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { LogLine } from "@/api/types";
import { FakeEventSource, mockFetch, renderWithProviders } from "@/test/utils";

import { runDetail, versionDetail } from "./fixtures";
import { RunDetailPage } from "./RunDetailPage";

const DOC = "d".repeat(64);

const parse = versionDetail();
const chunk = versionDetail({
  id: 11,
  label: "default/chunk@v3",
  node: "chunk",
  seq: 3,
  failed: 0,
  inputs: [{ node: "parse", version: versionDetail({ id: 9, label: "default/parse@v1" }), current: false }],
});

const logLines: LogLine[] = [
  { ts: "2026-10-19T12:00:01Z", level: "INFO", kind: "event", message: "run started", event: "run_started" },
  { ts: "2026-10-19T12:00:02Z", level: "WARNING", kind: "warning", message: "slow page", node: "parse", doc_id: DOC },
];

function routes(extra: Record<string, unknown> = {}) {
  return {
    "GET /api/runs/:id": runDetail({ versions: [parse, chunk], request: { snapshot_id: 5 } }),
    "GET /api/runs/:id/logs": logLines,
    "GET /api/runs/:id/metrics": {
      run: {},
      nodes: [{ node: "parse", metrics: { wall_s: 12.5, docs_processed: 5, cache_hit_rate: 0.25, ocr_pages: 3, pages: 10 } }],
      slowest: [{ node: "parse", doc_id: DOC, filename: "Bridge.pdf", duration_s: 4.2, peak_rss_mb: 1536 }],
    },
    "GET /api/versions/:id/docs": {
      total: 1,
      items: [
        {
          doc_id: DOC,
          status: "error",
          cached: false,
          filename: "Bridge.pdf",
          path: "Bridge.pdf",
          job_number: "P-1",
          project: null,
          error_type: "ValueError",
          error_message: "bad table",
          duration_s: 1,
          peak_rss_mb: null,
          metrics: {},
          issues: { error: 0, warning: 0 },
        },
      ],
    },
    "GET /api/versions/:id/docs/:doc": { traceback: "Traceback (most recent call last):\n  boom" },
    ...extra,
  };
}

describe("RunDetailPage", () => {
  it("shows versions, lineage, timings and failed docs with tracebacks", async () => {
    const fetchMock = mockFetch(routes());
    await renderWithProviders(<RunDetailPage />, { path: "/runs/7" });

    expect(await screen.findByText("Run #7")).toBeInTheDocument();
    expect(screen.getByText("#5")).toBeInTheDocument();
    expect(screen.getAllByText("#3")).toHaveLength(2);
    expect(screen.getAllByText("abcdef0")).toHaveLength(3);
    expect(screen.getByText("default/parse@v1")).toBeInTheDocument();
    expect(screen.getByText("Not current")).toBeInTheDocument();
    expect(await screen.findByText("3 / 10")).toBeInTheDocument();
    expect(screen.getByText("1.5 GB")).toBeInTheDocument();

    expect(await screen.findByText("ValueError")).toBeInTheDocument();
    expect(screen.getByText("bad table")).toBeInTheDocument();
    // Only the version with failures is queried.
    const docCalls = fetchMock.mock.calls
      .map(([r]) => (r as Request).url)
      .filter((u) => /\/api\/versions\/\d+\/docs\?/.test(u));
    expect(docCalls).toEqual([expect.stringContaining("/api/versions/10/docs?status=error")]);

    await userEvent.click(screen.getByRole("button", { name: "Traceback for Bridge.pdf" }));
    expect(await screen.findByText(/boom/)).toBeInTheDocument();
    const docLink = screen.getAllByRole("link").find((a) => a.getAttribute("href")?.includes("/docs/"));
    expect(docLink).toHaveAttribute("href", `/dags/default/docs/${DOC}?node=parse&version=10`);
  });

  it("binds log filters to the search params", async () => {
    const fetchMock = mockFetch(routes());
    const { router } = await renderWithProviders(<RunDetailPage />, { path: "/runs/7?level=WARNING" });

    const table = (await screen.findByText("slow page")).closest("table")!;
    expect(within(table).getByText("run_started")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([r]) => (r as Request).url.includes("/api/runs/7/logs?level=WARNING"))).toBe(true);

    await userEvent.click(screen.getByRole("combobox", { name: "Node" }));
    await userEvent.click(await screen.findByRole("option", { name: "parse" }));
    await waitFor(() => expect(router.state.location.search).toEqual({ level: "WARNING", node: "parse" }));

    // Clicking a doc in the log filters by it.
    await userEvent.click(within(table).getByRole("button", { name: "Bridge.pdf" }));
    await waitFor(() => expect(router.state.location.search).toEqual({ level: "WARNING", node: "parse", doc: DOC }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([r]) => {
          const url = new URL((r as Request).url);
          return url.pathname === "/api/runs/7/logs" && url.searchParams.get("doc") === DOC;
        }),
      ).toBe(true),
    );

    await userEvent.click(screen.getByRole("button", { name: "Clear document" }));
    await waitFor(() => expect(router.state.location.search).toEqual({ level: "WARNING", node: "parse" }));
  });

  it("updates per-node progress live from the event stream", async () => {
    mockFetch(routes({ "GET /api/runs/:id": runDetail({ status: "running", versions: [] }) }));
    await renderWithProviders(<RunDetailPage />, { path: "/runs/7" });
    await screen.findByText("Run #7");
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();

    const source = FakeEventSource.last("/api/runs/7/events")!;
    const send = (id: number, kind: string, payload: Record<string, unknown> = {}) =>
      act(() => source.emit(kind, { id, ts: "2026-10-19T12:00:00Z", kind, payload }, String(id)));

    send(1, "run_started", { docs: 4, nodes: ["parse", "chunk"], snapshot_id: 3 });
    send(2, "node_started", { node: "parse" });
    send(3, "doc_finished", { node: "parse", doc_id: "a" });
    send(4, "doc_cached", { node: "parse", doc_id: "b" });
    send(4, "doc_cached", { node: "parse", doc_id: "b" });

    const row = screen.getByTestId("progress-parse");
    expect(within(row).getByText("2 / 4")).toBeInTheDocument();
    expect(within(row).getByText("Running")).toBeInTheDocument();
    expect(within(row).getByText(/1 processed · 1 cached/)).toBeInTheDocument();
    expect(within(screen.getByTestId("progress-chunk")).getByText("0 / 4")).toBeInTheDocument();

    send(5, "doc_failed", { node: "parse", doc_id: "c" });
    expect(within(row).getByText("3 / 4")).toBeInTheDocument();
    expect(within(row).getByText("1 failed")).toBeInTheDocument();

    send(6, "run_finished", { status: "cancelled" });
    expect(source.closed).toBe(true);
  });
});
