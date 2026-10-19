import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { DocRef, SnapshotDiffOut, SnapshotOut, SourceOut, SyncOut } from "@/api/types";
import { json, mockFetch, renderWithProviders } from "@/test/utils";

import { SourcesPage } from "./SourcesPage";

function snapshot(id: number, source = "default", docs = 3): SnapshotOut {
  return { id, source, created_at: "2026-10-19T12:00:00Z", doc_count: docs, file_count: docs + 1 };
}

function doc(id: string, filename: string): DocRef {
  return { doc_id: id, path: `2292 SZEKERES/${filename}`, filename, job_number: "2292", project: "SZEKERES" };
}

function diff(old: SnapshotOut | null, next: SnapshotOut, extra: Partial<SnapshotDiffOut> = {}): SnapshotDiffOut {
  return { old, new: next, added: [], removed: [], changed_paths: [], unchanged: 0, ...extra };
}

const sources: SourceOut[] = [
  {
    name: "default",
    folders: ["/Users/Shared/proposals", ".ingest/uploads"],
    glob: "**/*.pdf",
    snapshots: [snapshot(3), snapshot(1)],
  },
  { name: "other", folders: ["/tmp/other"], glob: "**/*.pdf", snapshots: [snapshot(2, "other")] },
];

const diffs: Record<string, SnapshotDiffOut> = {
  "3": diff(snapshot(1), snapshot(3), {
    added: [doc("a", "New Proposal.pdf")],
    removed: [doc("b", "Old Proposal.pdf")],
    changed_paths: ["2292 SZEKERES/Changed.pdf"],
    unchanged: 2,
  }),
  "1": diff(null, snapshot(1), { added: [doc("c", "First.pdf"), doc("d", "Second.pdf")] }),
  "2": diff(null, snapshot(2, "other"), { added: [doc("e", "Other.pdf")] }),
};

function mockApi(extra: Parameters<typeof mockFetch>[0] = {}) {
  return mockFetch({
    "GET /api/sources": sources,
    "GET /api/snapshots/:id/diff": (_req: Request, { id }: Record<string, string>) => diffs[id],
    ...extra,
  });
}

function snapshotRows() {
  return within(screen.getByRole("table", { name: "Snapshots" })).getAllByRole("row").slice(1);
}

describe("SourcesPage", () => {
  it("defaults to the first source and its newest snapshot", async () => {
    mockApi();
    await renderWithProviders(<SourcesPage />, { path: "/sources" });
    expect(await screen.findByText("#1 → #3")).toBeInTheDocument();
    expect(screen.getByText("/Users/Shared/proposals")).toBeInTheDocument();
    expect(snapshotRows().map((r) => r.textContent?.slice(0, 2))).toEqual(["#3", "#1"]);
    expect(snapshotRows()[0]).toHaveAttribute("data-state", "selected");
    expect(screen.getByText("New Proposal.pdf")).toBeInTheDocument();
    expect(screen.getByText("Old Proposal.pdf")).toBeInTheDocument();
    expect(screen.getByText("2292 SZEKERES/Changed.pdf")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Added\s*1/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Changed\s*1/ })).toBeInTheDocument();
    expect(screen.getByText("Unchanged").nextSibling).toHaveTextContent("2");
  });

  it("selects snapshots and sources through search params", async () => {
    mockApi();
    const { router } = await renderWithProviders(<SourcesPage />, { path: "/sources" });
    await screen.findByText("#1 → #3");

    await userEvent.click(snapshotRows()[1]);
    expect(router.state.location.search).toEqual({ source: "default", snapshot: 1 });
    expect(await screen.findByText("First.pdf")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Removed\s*0/ })).toBeDisabled();

    await userEvent.click(screen.getByText("other"));
    expect(router.state.location.search).toEqual({ source: "other" });
    expect(await screen.findByText("Other.pdf")).toBeInTheDocument();
  });

  it("derives the source from a snapshot param", async () => {
    mockApi();
    await renderWithProviders(<SourcesPage />, { path: "/sources?snapshot=2" });
    expect(await screen.findByText("Other.pdf")).toBeInTheDocument();
    expect(snapshotRows()).toHaveLength(1);
  });

  it("collapses a diff section", async () => {
    mockApi();
    await renderWithProviders(<SourcesPage />, { path: "/sources" });
    await screen.findByText("New Proposal.pdf");
    await userEvent.click(screen.getByRole("button", { name: /Added\s*1/ }));
    expect(screen.queryByText("New Proposal.pdf")).not.toBeInTheDocument();
  });

  it("selects the snapshot a sync created and shows its diff", async () => {
    const created: SyncOut = {
      snapshot: snapshot(4),
      created: true,
      diff: diff(snapshot(3), snapshot(4), { added: [doc("f", "Synced.pdf")], unchanged: 3 }),
    };
    diffs["4"] = created.diff;
    mockApi({ "POST /api/sources/:name/sync": created });
    const { router } = await renderWithProviders(<SourcesPage />, { path: "/sources" });
    await screen.findByText("#1 → #3");

    await userEvent.click(screen.getAllByRole("button", { name: "Sync" })[0]);
    expect(await screen.findByText("Synced.pdf")).toBeInTheDocument();
    expect(router.state.location.search).toEqual({ source: "default", snapshot: 4 });
    expect(screen.queryByText("No changes")).not.toBeInTheDocument();
  });

  it("says when a sync found no changes", async () => {
    const unchanged: SyncOut = { snapshot: snapshot(3), created: false, diff: diff(snapshot(3), snapshot(3)) };
    mockApi({ "POST /api/sources/:name/sync": unchanged });
    const { router } = await renderWithProviders(<SourcesPage />, { path: "/sources?snapshot=1" });
    await screen.findByText("First.pdf");

    await userEvent.click(screen.getAllByRole("button", { name: "Sync" })[0]);
    expect(await screen.findByText("No changes")).toBeInTheDocument();
    expect(router.state.location.search).toEqual({ source: "default", snapshot: 3 });
    expect(await screen.findByText("#1 → #3")).toBeInTheDocument();
  });

  it("shows sync errors", async () => {
    mockApi({ "POST /api/sources/:name/sync": () => json({ detail: "not a directory" }, 400) });
    await renderWithProviders(<SourcesPage />, { path: "/sources" });
    await screen.findByText("#1 → #3");
    await userEvent.click(screen.getAllByRole("button", { name: "Sync" })[0]);
    expect(await screen.findByText("not a directory")).toBeInTheDocument();
  });
});
