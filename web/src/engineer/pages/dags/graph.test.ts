import { describe, expect, it } from "vitest";

import { dagDetail, nodeInfo } from "./fixtures";
import { layoutDag, NODE_HEIGHT } from "./graph";

describe("layoutDag", () => {
  it("maps nodes and input edges and lays them out top to bottom", () => {
    const { nodes, edges } = layoutDag(dagDetail.nodes, "chunk");
    expect(nodes.map((n) => [n.id, n.type, n.data.info.name, n.data.selected])).toEqual([
      ["parse", "dag", "parse", false],
      ["chunk", "dag", "chunk", true],
      ["embed", "dag", "embed", false],
    ]);
    expect(edges.map((e) => [e.id, e.source, e.target])).toEqual([
      ["parse->chunk", "parse", "chunk"],
      ["chunk->embed", "chunk", "embed"],
    ]);
    const [parse, chunk, embed] = nodes.map((n) => n.position);
    expect(parse.x).toBe(chunk.x);
    expect(chunk.x).toBe(embed.x);
    expect(chunk.y - parse.y).toBeGreaterThanOrEqual(NODE_HEIGHT);
    expect(embed.y - chunk.y).toBeGreaterThanOrEqual(NODE_HEIGHT);
  });

  it("places siblings side by side and drops inputs outside the DAG", () => {
    const { nodes, edges } = layoutDag([
      nodeInfo("a", ["external"]),
      nodeInfo("b", ["a"]),
      nodeInfo("c", ["a"]),
    ]);
    expect(edges.map((e) => e.id)).toEqual(["a->b", "a->c"]);
    const [a, b, c] = nodes.map((n) => n.position);
    expect(b.y).toBe(c.y);
    expect(b.y).toBeGreaterThan(a.y);
    expect(b.x).not.toBe(c.x);
  });
});
