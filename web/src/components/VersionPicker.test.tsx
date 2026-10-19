import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { VersionSummary } from "@/api/types";
import { mockFetch, renderWithProviders } from "@/test/utils";

import { VersionPicker } from "./VersionPicker";

function version(id: number, seq: number, extra: Partial<VersionSummary> = {}): VersionSummary {
  return {
    id,
    label: `default/chunk@v${seq}`,
    dag: "default",
    node: "chunk",
    seq,
    status: "complete",
    scratch: false,
    current: false,
    commit: "abcdef0123",
    dirty: false,
    node_fp: "f".repeat(64),
    snapshot_id: 1,
    run_id: 1,
    artifact_type: "chunk",
    created_at: "2026-10-19T12:00:00Z",
    finalized_at: "2026-10-19T12:01:00Z",
    docs: 10,
    ok: 10,
    failed: 0,
    skipped: 0,
    ...extra,
  };
}

const versions = [
  version(3, 3),
  version(2, 2, { current: true }),
  version(4, 0, { seq: null, label: "default/chunk@scratch-4", scratch: true }),
  version(1, 1),
];

function mockApi() {
  mockFetch({
    "GET /api/dags/:dag/nodes/:node/versions": versions,
    "GET /api/dags/:dag": { name: "default", nodes: [{ name: "chunk", candidates: [versions[0]] }] },
  });
}

describe("VersionPicker", () => {
  it("lists versions with their roles and reports the choice", async () => {
    mockApi();
    const onChange = vi.fn();
    await renderWithProviders(<VersionPicker dag="default" node="chunk" value={2} onChange={onChange} />);
    expect(await screen.findByText("default/chunk@v2")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("combobox", { name: "Version" }));
    const options = await screen.findAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual([
      "default/chunk@v3Candidate",
      "default/chunk@v2Current",
      "default/chunk@scratch-4Scratch",
      "default/chunk@v1",
    ]);
    await userEvent.click(options[0]);
    expect(onChange).toHaveBeenCalledWith(3);
  });

  it("filters by role", async () => {
    mockApi();
    await renderWithProviders(
      <VersionPicker dag="default" node="chunk" value={undefined} onChange={() => {}} include={["current", "candidate"]} />,
    );
    await userEvent.click(screen.getByRole("combobox", { name: "Version" }));
    const options = await screen.findAllByRole("option");
    expect(options.map((o) => o.textContent)).toEqual(["default/chunk@v3Candidate", "default/chunk@v2Current"]);
  });
});
