import dagre from "@dagrejs/dagre";
import type { Edge, Node } from "@xyflow/react";

import type { NodeInfo } from "@/api/types";

export const NODE_WIDTH = 208;
export const NODE_HEIGHT = 76;

export type DagNodeData = { info: NodeInfo; selected: boolean };
export type DagFlowNode = Node<DagNodeData, "dag">;

/** React Flow nodes and edges for a DAG, laid out top to bottom by dagre. Edges run from each
 * input to the node that reads it; inputs that aren't nodes of the DAG are left out. */
export function layoutDag(nodes: NodeInfo[], selected?: string): { nodes: DagFlowNode[]; edges: Edge[] } {
  const names = new Set(nodes.map((n) => n.name));
  const edges: Edge[] = nodes.flatMap((n) =>
    n.inputs
      .filter((input) => names.has(input))
      .map((input) => ({ id: `${input}->${n.name}`, source: input, target: n.name })),
  );

  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 40, ranksep: 56 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of nodes) g.setNode(n.name, { width: NODE_WIDTH, height: NODE_HEIGHT });
  for (const e of edges) g.setEdge(e.source, e.target);
  dagre.layout(g);

  return {
    nodes: nodes.map((info) => {
      const { x, y } = g.node(info.name);
      return {
        id: info.name,
        type: "dag",
        position: { x: x - NODE_WIDTH / 2, y: y - NODE_HEIGHT / 2 },
        data: { info, selected: info.name === selected },
      };
    }),
    edges,
  };
}
