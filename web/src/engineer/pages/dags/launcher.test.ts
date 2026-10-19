import { describe, expect, it } from "vitest";

import { nodePlan, runPlan, version } from "./fixtures";
import { buildRunRequest, externalInputs, initialLauncherState, shownCode } from "./launcher";

describe("initialLauncherState", () => {
  it("selects the node it was opened from, or the whole DAG", () => {
    expect(initialLauncherState("chunk").selection).toBe("node");
    expect(initialLauncherState().selection).toBe("all");
  });

  it("picks the documents it was opened with", () => {
    const s = initialLauncherState(undefined, ["d1", "d2"]);
    expect(s.docsMode).toBe("pick");
    expect(s.picked).toEqual(["d1", "d2"]);
    expect(initialLauncherState().docsMode).toBe("all");
  });
});

describe("buildRunRequest", () => {
  const base = initialLauncherState("chunk");

  it("builds the default request", () => {
    expect(buildRunRequest("default", "chunk", base)).toEqual({
      dag: "default",
      target: "chunk",
      selection: "node",
      snapshot_id: null,
      input_versions: {},
      recompute: "reuse",
      docs: "all",
      code: "auto",
      sample_seed: 0,
    });
  });

  it("drops the target for whole-DAG runs and needs one otherwise", () => {
    expect(buildRunRequest("default", "chunk", { ...base, selection: "all" })?.target).toBeNull();
    expect(buildRunRequest("default", undefined, { ...base, selection: "downstream" })).toBeNull();
  });

  it("maps the document modes", () => {
    expect(buildRunRequest("default", "chunk", { ...base, docsMode: "sample", sampleSize: 3 })?.docs).toBe(3);
    expect(buildRunRequest("default", "chunk", { ...base, docsMode: "sample", sampleSize: 0 })).toBeNull();
    expect(buildRunRequest("default", "chunk", { ...base, docsMode: "sample", sampleSize: NaN })).toBeNull();
    expect(buildRunRequest("default", "chunk", { ...base, docsMode: "pick", picked: ["d1"] })?.docs).toEqual(["d1"]);
    expect(buildRunRequest("default", "chunk", { ...base, docsMode: "pick", picked: [] })).toBeNull();
  });

  it("passes overrides, snapshot, recompute and the chosen code", () => {
    const request = buildRunRequest("default", "chunk", {
      ...base,
      snapshotId: 3,
      inputVersions: { parse: 9 },
      recompute: "force",
      code: "working",
    });
    expect(request).toMatchObject({ snapshot_id: 3, input_versions: { parse: 9 }, recompute: "force", code: "working" });
  });
});

describe("externalInputs", () => {
  it("lists inputs read from existing versions once, skipping ones produced in the run", () => {
    const parse = version(1, "parse", 1, { current: true });
    const plan = runPlan({
      nodes: [
        nodePlan("chunk", { inputs: [{ node: "parse", version: parse, fingerprint: "x" }] }),
        nodePlan("embed", { inputs: [{ node: "chunk", version: null, fingerprint: "y" }] }),
        nodePlan("index", { inputs: [{ node: "parse", version: parse, fingerprint: "x" }] }),
      ],
    });
    expect(externalInputs(plan)).toEqual([{ node: "parse", version: parse }]);
  });
});

describe("shownCode", () => {
  it("prefers the user's choice, else working tree when the plan is dirty", () => {
    expect(shownCode("committed", runPlan({ dirty: true }))).toBe("committed");
    expect(shownCode(null, runPlan({ dirty: true }))).toBe("working");
    expect(shownCode(null, runPlan({ dirty: false }))).toBe("committed");
    expect(shownCode(null, undefined)).toBeUndefined();
  });
});
