import { describe, expect, it } from "vitest";

import { defaultVersions, embedOptions, type EmbedNode } from "./embedVersions";
import { node, version } from "./fixtures";

function embedNode(dag: string, versions: ReturnType<typeof version>[], candidates: number[] = []): EmbedNode {
  return {
    dag,
    node: node("embed", {
      current: versions.find((v) => v.current) ?? null,
      candidates: versions.filter((v) => candidates.includes(v.id)),
    }),
    versions,
  };
}

describe("defaultVersions", () => {
  it("is the current version against the newest candidate", () => {
    const vs = [version(4), version(3), version(2, { current: true }), version(1)];
    expect(defaultVersions([embedNode("default", vs, [1, 3])])).toEqual({ a: 2, b: 3 });
  });

  it("prefers candidates of the current version's node", () => {
    const other = embedNode("alt", [version(9, { dag: "alt" })], [9]);
    const home = embedNode("default", [version(2), version(1, { current: true })], [2]);
    expect(defaultVersions([other, home])).toEqual({ a: 1, b: 2 });
  });

  it("falls back to the newest other version, then to a single side", () => {
    const vs = [version(3, { scratch: true }), version(2, { current: true }), version(1)];
    expect(defaultVersions([embedNode("default", vs)])).toEqual({ a: 2, b: 3 });
    expect(defaultVersions([embedNode("default", [version(1, { current: true })])])).toEqual({ a: 1, b: undefined });
  });

  it("uses the newest version without a current one", () => {
    expect(defaultVersions([embedNode("default", [version(2), version(1)])])).toEqual({ a: 2, b: 1 });
    expect(defaultVersions([])).toEqual({});
  });
});

describe("embedOptions", () => {
  it("lists every version with its role", () => {
    const vs = [version(3, { scratch: true }), version(2), version(1, { current: true })];
    expect(embedOptions([embedNode("default", vs, [2])]).map((o) => [o.version.id, o.role])).toEqual([
      [3, "scratch"],
      [2, "candidate"],
      [1, "current"],
    ]);
  });
});
