import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { RunPlanOut, RunRequestIn, SnapshotDocOut } from "@/api/types";
import { json, mockFetch, renderWithProviders } from "@/test/utils";

import { dagDetail, nodePlan, runPlan, version } from "./fixtures";
import { RunLauncher } from "./RunLauncher";

const parseV1 = version(1, "parse", 1, { current: true });
const parseV7 = version(7, "parse", 2);

const sources = [
  {
    name: "proposals",
    folders: ["/data/proposals"],
    glob: "**/*.pdf",
    snapshots: [
      { id: 2, source: "proposals", created_at: "2026-10-16T18:33:37Z", doc_count: 6, file_count: 7 },
      { id: 1, source: "proposals", created_at: "2026-10-15T17:33:37Z", doc_count: 6, file_count: 7 },
    ],
  },
];

function snapshotDoc(doc_id: string, filename: string): SnapshotDocOut {
  return { doc_id, path: filename, filename, job_number: null, project: null, paths: [filename], size: 1, flags: [], flag_reason: null };
}

function setup(plan: (request: RunRequestIn) => RunPlanOut | Response = () => runPlan()) {
  const plans: RunRequestIn[] = [];
  const started: RunRequestIn[] = [];
  mockFetch({
    "GET /api/dags/:dag": dagDetail,
    "GET /api/sources": sources,
    "GET /api/dags/:dag/nodes/:node/versions": [parseV7, parseV1],
    "GET /api/snapshots/:id/docs": [snapshotDoc("d1", "a.pdf"), snapshotDoc("d2", "b.pdf")],
    "POST /api/runs/plan": async (request: Request) => {
      const body = (await request.json()) as RunRequestIn;
      plans.push(body);
      return plan(body);
    },
    "POST /api/runs": async (request: Request) => {
      started.push((await request.json()) as RunRequestIn);
      return { run_id: 12 };
    },
  });
  return { plans, started, lastPlan: () => plans[plans.length - 1] };
}

function choice(group: string, name: string): HTMLElement {
  return within(screen.getByRole("radiogroup", { name: group })).getByRole("radio", { name });
}

async function renderLauncher(props: { node?: string; docIds?: string[] } = { node: "chunk" }) {
  const onOpenChange = vi.fn();
  const result = await renderWithProviders(<RunLauncher dag="default" open onOpenChange={onOpenChange} {...props} />, {
    path: "/dags/default",
  });
  return { ...result, onOpenChange };
}

describe("RunLauncher", () => {
  it("plans the preselected node and shows the summary", async () => {
    const api = setup(() =>
      runPlan({
        scratch: true,
        scratch_reasons: ["working tree"],
        nodes: [nodePlan("chunk"), nodePlan("embed", { docs_to_process: 1, docs_cached: 3, docs_blocked: 2 })],
      }),
    );
    await renderLauncher();
    const plan = within(await screen.findByRole("region", { name: "Plan" }));
    expect(api.lastPlan()).toMatchObject({ dag: "default", target: "chunk", selection: "node", docs: "all", code: "auto" });
    expect(plan.getByText("Scratch")).toBeInTheDocument();
    expect(plan.getByText("working tree")).toBeInTheDocument();
    expect(plan.getByText("4 to process · 2 cached")).toBeInTheDocument();
    expect(plan.getByText("1 to process · 3 cached · 2 blocked")).toBeInTheDocument();
    expect(screen.getByText("chunk, embed")).toBeInTheDocument();
  });

  it("re-plans when the node selection changes", async () => {
    const api = setup();
    await renderLauncher();
    await waitFor(() => expect(api.plans).toHaveLength(1));
    await userEvent.click(choice("Nodes", "+ downstream"));
    await waitFor(() => expect(api.lastPlan()).toMatchObject({ target: "chunk", selection: "downstream" }));
    await userEvent.click(choice("Nodes", "all"));
    await waitFor(() => expect(api.lastPlan()).toMatchObject({ target: null, selection: "all" }));
  });

  it("offers only the whole DAG without a node", async () => {
    setup();
    await renderLauncher({});
    expect(choice("Nodes", "node")).toBeDisabled();
    expect(choice("Nodes", "all")).toHaveAttribute("aria-checked", "true");
  });

  it("preselects working tree when the plan is dirty and sends the user's choice", async () => {
    const api = setup(() => runPlan({ dirty: true, code: "working" }));
    await renderLauncher();
    await waitFor(() => expect(choice("Code", "working tree")).toHaveAttribute("aria-checked", "true"));
    expect(api.lastPlan().code).toBe("auto");
    await userEvent.click(choice("Code", "committed"));
    await waitFor(() => expect(api.lastPlan().code).toBe("committed"));
  });

  it("samples N documents", async () => {
    const api = setup();
    await renderLauncher();
    await userEvent.click(choice("Documents", "sample"));
    await waitFor(() => expect(api.lastPlan().docs).toBe(10));
    const size = screen.getByRole("spinbutton", { name: "Sample size" });
    await userEvent.clear(size);
    await userEvent.type(size, "3");
    await waitFor(() => expect(api.lastPlan().docs).toBe(3));
  });

  it("picks the documents it was opened with and edits the pick", async () => {
    const api = setup();
    await renderLauncher({ node: "chunk", docIds: ["d1"] });
    expect(choice("Documents", "pick")).toHaveAttribute("aria-checked", "true");
    await waitFor(() => expect(api.lastPlan().docs).toEqual(["d1"]));
    expect(await screen.findByRole("button", { name: "Remove a.pdf" })).toBeInTheDocument();

    await userEvent.click(await screen.findByRole("option", { name: "b.pdf" }));
    await waitFor(() => expect(api.lastPlan().docs).toEqual(["d1", "d2"]));
    await userEvent.click(screen.getByRole("button", { name: "Remove a.pdf" }));
    await waitFor(() => expect(api.lastPlan().docs).toEqual(["d2"]));
  });

  it("overrides an input version read from outside the run", async () => {
    const api = setup((request) =>
      runPlan({
        nodes: [
          nodePlan("chunk", {
            inputs: [{ node: "parse", version: request.input_versions?.parse === 7 ? parseV7 : parseV1, fingerprint: "x" }],
          }),
        ],
      }),
    );
    await renderLauncher();
    const picker = await screen.findByRole("combobox", { name: "Version" });
    await waitFor(() => expect(picker).toHaveTextContent("default/parse@v1"));
    await userEvent.click(picker);
    await userEvent.click(await screen.findByRole("option", { name: /default\/parse@v2/ }));
    await waitFor(() => expect(api.lastPlan().input_versions).toEqual({ parse: 7 }));
  });

  it("shows plan errors inline and blocks starting", async () => {
    setup(() => json({ detail: "Node 'chunk' has no current input version" }, 400));
    await renderLauncher();
    expect(await screen.findByText("Node 'chunk' has no current input version")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start run" })).toBeDisabled();
  });

  it("starts the run, closes and navigates to it", async () => {
    const api = setup(() => runPlan({ nodes: [nodePlan("chunk")] }));
    const { router, onOpenChange } = await renderLauncher();
    await screen.findByRole("region", { name: "Plan" });
    await userEvent.click(choice("Recompute", "force"));
    await userEvent.click(screen.getByRole("button", { name: "Start run" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/runs/12"));
    expect(api.started).toEqual([
      expect.objectContaining({ dag: "default", target: "chunk", selection: "node", recompute: "force", code: "auto" }),
    ]);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
