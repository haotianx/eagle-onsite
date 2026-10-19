import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { NodeInfo, VersionDocDetail, VersionSummary } from "@/api/types";
import type { PdfViewerProps } from "@/components/pdf/PdfViewer";
import type { RunLauncherProps } from "@/engineer/pages/dags/RunLauncher";
import { mockFetch, renderWithProviders } from "@/test/utils";

import { DocumentPage } from "./DocumentPage";

vi.mock("react-pdf", () => ({ Document: () => null, Page: () => null, pdfjs: { GlobalWorkerOptions: {} } }));

vi.mock("@/components/pdf/PdfViewer", () => ({
  PdfViewer: ({ page, highlights }: PdfViewerProps) => (
    <div data-testid="pdf" data-page={page ?? ""} data-highlights={JSON.stringify(highlights ?? [])} />
  ),
}));

// jsdom has no layout, so the panel group's separator hit-test claims every pointerdown.
vi.mock("@/components/ui/resizable", () => ({
  ResizablePanelGroup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  ResizablePanel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  ResizableHandle: () => null,
}));

vi.mock("@/engineer/pages/dags/RunLauncher", () => ({
  RunLauncher: ({ open, node, docIds }: RunLauncherProps) =>
    open ? <div data-testid="launcher" data-node={node} data-docs={docIds?.join(",")} /> : null,
}));

function version(id: number, node: string, overrides: Partial<VersionSummary> = {}): VersionSummary {
  return {
    id,
    label: `default/${node}@v${id}`,
    dag: "default",
    node,
    seq: id,
    status: "complete",
    scratch: false,
    current: false,
    commit: "abc",
    dirty: false,
    node_fp: "fp",
    snapshot_id: 1,
    run_id: 100 + id,
    artifact_type: node === "ocr" ? "parse" : "chunk",
    created_at: "2026-10-01T00:00:00Z",
    finalized_at: null,
    docs: 1,
    ok: 1,
    failed: 0,
    skipped: 0,
    ...overrides,
  };
}

function nodeInfo(name: string, current: VersionSummary | null): NodeInfo {
  return {
    name,
    script: `${name}.py`,
    params: {},
    inputs: [],
    checks: null,
    timeout: 300,
    workers: 1,
    artifact_type: null,
    state: "fresh",
    reasons: [],
    dirty: false,
    fingerprint: "fp",
    current,
    candidates: [],
    scratch: [],
    doc_count: 1,
    failure_count: 0,
    active_run_id: null,
  };
}

const parseRecord = {
  docling: {
    body: { children: [{ $ref: "#/texts/0" }] },
    texts: [
      {
        label: "section_header",
        text: "Scope of work",
        prov: [{ page_no: 2, bbox: { l: 72, t: 720, r: 300, b: 700, coord_origin: "BOTTOMLEFT" } }],
      },
    ],
    pages: { "2": { page_no: 2, size: { width: 612, height: 792 } } },
  },
  pages: [
    { page: 1, width: 612, height: 792, text_chars: 0, ocr: true },
    { page: 2, width: 612, height: 792, text_chars: 13, ocr: false },
  ],
};

const chunkRecord = [
  {
    chunk_id: "c1",
    seq: 0,
    text: "Pumps and valves",
    pages: [3],
    section: ["Scope"],
    tokens: 12,
    regions: [
      { page: 3, x0: 10, y0: 20, x1: 30, y1: 40 },
      { page: 3, x0: 10, y0: 50, x1: 30, y1: 60 },
    ],
  },
];

function detail(v: VersionSummary, overrides: Partial<VersionDocDetail> = {}): VersionDocDetail {
  return {
    doc_id: "d1",
    status: "ok",
    cached: false,
    filename: "spec.pdf",
    path: "2292 SZEKERES/spec.pdf",
    job_number: "2292",
    project: "SZEKERES",
    error_type: null,
    error_message: null,
    duration_s: 2.5,
    peak_rss_mb: 512,
    metrics: {},
    issues: { error: 0, warning: 0 },
    version: v,
    paths: ["2292 SZEKERES/spec.pdf"],
    traceback: null,
    worker_pid: 4242,
    kind: v.node === "ocr" ? "json" : "parquet",
    record: v.node === "ocr" ? parseRecord : chunkRecord,
    omitted_columns: [],
    issue_list: [],
    debug_files: [],
    ...overrides,
  };
}

const ocr1 = version(1, "ocr", { current: true });
const ocr2 = version(2, "ocr");
const chunk3 = version(3, "chunk", { current: true });
const chunk4 = version(4, "chunk");

function mockApi(details: Record<number, VersionDocDetail> = {}) {
  const all = [ocr1, ocr2, chunk3, chunk4];
  return mockFetch({
    "GET /api/dags/:dag": { name: "default", nodes: [nodeInfo("ocr", ocr1), nodeInfo("chunk", chunk3)] },
    "GET /api/dags/:dag/nodes/:node/versions": (_r: Request, p: Record<string, string>) =>
      all.filter((v) => v.node === p.node).reverse(),
    "GET /api/versions/:id/docs/:doc": (_r: Request, p: Record<string, string>) => {
      const id = Number(p.id);
      return details[id] ?? detail(all.find((v) => v.id === id)!);
    },
    "GET /api/runs/:id/logs": [{ ts: "2026-10-01T10:11:12Z", level: "INFO", kind: "log", message: "parsed 2 pages" }],
  });
}

const pdf = () => screen.getByTestId("pdf");
const highlights = () => JSON.parse(pdf().dataset.highlights!) as unknown[];

describe("DocumentPage", () => {
  it("opens the first node's current version and highlights hovered parse elements", async () => {
    const fetchMock = mockApi();
    await renderWithProviders(<DocumentPage />, { path: "/dags/default/docs/d1?page=4" });

    const element = await screen.findByText("Scope of work");
    expect(screen.getByRole("tab", { name: "ocr", selected: true })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Version" })).toHaveTextContent("default/ocr@v1");
    expect(pdf().dataset.page).toBe("4");
    expect(await screen.findByText("parsed 2 pages")).toBeInTheDocument();
    const logsUrl = fetchMock.mock.calls.map(([r]) => new URL((r as Request).url)).find((u) => u.pathname === "/api/runs/101/logs");
    expect(Object.fromEntries(logsUrl!.searchParams)).toEqual({ node: "ocr", doc: "d1" });

    await userEvent.hover(element);
    expect(highlights()).toEqual([{ page: 2, x0: 72, y0: 72, x1: 300, y1: 92 }]);
    await userEvent.unhover(element);
    expect(highlights()).toEqual([]);

    await userEvent.click(element);
    expect(pdf().dataset.page).toBe("2");
    expect(highlights()).toHaveLength(1);
  });

  it("reads the tab and version from the URL and highlights all of a chunk's regions", async () => {
    mockApi();
    await renderWithProviders(<DocumentPage />, { path: "/dags/default/docs/d1?node=chunk&version=4" });

    const chunk = await screen.findByText("Pumps and valves");
    expect(screen.getByRole("tab", { name: "chunk", selected: true })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Version" })).toHaveTextContent("default/chunk@v4");
    await userEvent.hover(chunk);
    expect(highlights()).toEqual(chunkRecord[0].regions);
  });

  it("remembers each tab's version and keeps the active one in the URL", async () => {
    mockApi();
    const { router } = await renderWithProviders(<DocumentPage />, {
      path: "/dags/default/docs/d1?node=ocr&version=2",
    });
    await screen.findByText("Scope of work");

    await userEvent.click(screen.getByRole("tab", { name: "chunk" }));
    await screen.findByText("Pumps and valves");
    expect(router.state.location.search).toEqual({ node: "chunk" });
    expect(screen.getByRole("combobox", { name: "Version" })).toHaveTextContent("default/chunk@v3");

    await userEvent.click(screen.getByRole("tab", { name: "ocr" }));
    await screen.findByText("Scope of work");
    expect(router.state.location.search).toEqual({ node: "ocr", version: 2 });
    expect(screen.getByRole("combobox", { name: "Version" })).toHaveTextContent("default/ocr@v2");
  });

  it("shows a failed doc's error and traceback", async () => {
    mockApi({
      1: detail(ocr1, {
        status: "error",
        record: null,
        error_type: "RuntimeError",
        error_message: "docling conversion failed",
        traceback: "Traceback (most recent call last):\n  File \"docling_parse.py\", line 99",
      }),
    });
    await renderWithProviders(<DocumentPage />, { path: "/dags/default/docs/d1" });

    expect(await screen.findByText("RuntimeError")).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText(/docling conversion failed/)).toBeInTheDocument();
    expect(screen.getByText(/line 99/)).toBeInTheDocument();
  });

  it("lists debug files for scratch versions only", async () => {
    const files = [
      { name: "page-1.png", size: 2048 },
      { name: "pages.json", size: 100 },
    ];
    const scratch = version(2, "ocr", { scratch: true });
    mockApi({ 1: detail(ocr1, { debug_files: files }), 2: detail(scratch, { debug_files: files }) });

    const first = await renderWithProviders(<DocumentPage />, { path: "/dags/default/docs/d1" });
    await screen.findByText("Scope of work");
    expect(screen.queryByText("Debug files")).toBeNull();
    first.unmount();

    await renderWithProviders(<DocumentPage />, { path: "/dags/default/docs/d1?node=ocr&version=2" });
    const link = await screen.findByRole("link", { name: "pages.json" });
    expect(link).toHaveAttribute("href", "/api/versions/2/docs/d1/debug/pages.json");
    expect(link).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("img", { name: "page-1.png" })).toHaveAttribute(
      "src",
      "/api/versions/2/docs/d1/debug/page-1.png",
    );
  });

  it("opens the run launcher for the active node and this doc", async () => {
    mockApi();
    await renderWithProviders(<DocumentPage />, { path: "/dags/default/docs/d1?node=chunk" });
    await screen.findByText("Pumps and valves");
    await userEvent.click(screen.getByRole("button", { name: "Run…" }));
    const launcher = screen.getByTestId("launcher");
    expect(launcher.dataset.node).toBe("chunk");
    expect(launcher.dataset.docs).toBe("d1");
  });
});
