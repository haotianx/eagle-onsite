import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { IssueOut, VersionDetail } from "@/api/types";
import { json, mockFetch, renderWithProviders } from "@/test/utils";

import { versionDetail, versionSummary } from "./fixtures";
import { PromoteDialog } from "./PromoteDialog";

const issues: IssueOut[] = [
  { id: 1, doc_id: "d1", filename: "a.pdf", source: "quality", level: "warning", code: "zero_chunks", message: "No chunks", page: null },
  { id: 2, doc_id: "d2", filename: "b.pdf", source: "script", level: "error", code: "bad", message: "Bad record", page: 3 },
];

function setup(detail: VersionDetail, promote: unknown = { version: versionSummary(5), replaced: null, warnings: [] }) {
  return mockFetch({
    "GET /api/versions/:id": detail,
    "GET /api/versions/:id/issues": issues,
    "POST /api/versions/:id/promote": promote,
  });
}

async function renderDialog(props: { onPromoted?(): void; onOpenChange?(open: boolean): void } = {}) {
  await renderWithProviders(
    <PromoteDialog versionId={5} open onOpenChange={props.onOpenChange ?? (() => {})} onPromoted={props.onPromoted} />,
  );
}

describe("PromoteDialog", () => {
  it("names the replaced version and lists issues", async () => {
    setup(versionDetail(5, { issues: { error: 1, warning: 1 } }));
    await renderDialog();
    expect(await screen.findByText("default/chunk@v2")).toBeInTheDocument();
    expect(screen.getByText("default/chunk@v5")).toBeInTheDocument();
    expect(screen.getByText("1 error")).toBeInTheDocument();
    expect(screen.getByText("1 warning")).toBeInTheDocument();
    expect(await screen.findByText("Bad record")).toBeInTheDocument();
    expect(screen.getByText("No chunks")).toBeInTheDocument();
    expect(screen.queryByText("Built from inputs that aren't current")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Promote" })).toBeEnabled();
  });

  it("says when nothing is current and warns about non-current inputs", async () => {
    setup(versionDetail(5, { replaces: null, noncurrent_inputs: ["ocr"] }));
    await renderDialog();
    expect(await screen.findByText("No current version")).toBeInTheDocument();
    expect(screen.getByText("Built from inputs that aren't current")).toBeInTheDocument();
    expect(screen.getByText("ocr")).toBeInTheDocument();
  });

  it("shows the reasons and disables Promote when not promotable", async () => {
    setup(versionDetail(5, { promotable: false, not_promotable_reasons: ["scratch version"] }));
    await renderDialog();
    expect(await screen.findByText("scratch version")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Promote" })).toBeDisabled();
  });

  it("promotes, then reports and closes", async () => {
    const fetchMock = setup(versionDetail(5));
    const onPromoted = vi.fn();
    const onOpenChange = vi.fn();
    await renderDialog({ onPromoted, onOpenChange });
    await screen.findByText("default/chunk@v2");
    await userEvent.click(screen.getByRole("button", { name: "Promote" }));
    await waitFor(() => expect(onPromoted).toHaveBeenCalled());
    expect(onOpenChange).toHaveBeenCalledWith(false);
    const post = fetchMock.mock.calls.map(([r]) => r as Request).find((r) => r.method === "POST");
    expect(new URL(post!.url).pathname).toBe("/api/versions/5/promote");
  });

  it("shows a 409 detail inline", async () => {
    setup(versionDetail(5), json({ detail: "already current" }, 409));
    const onOpenChange = vi.fn();
    await renderDialog({ onOpenChange });
    await screen.findByText("default/chunk@v2");
    await userEvent.click(screen.getByRole("button", { name: "Promote" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("already current");
    expect(onOpenChange).not.toHaveBeenCalled();
  });
});
