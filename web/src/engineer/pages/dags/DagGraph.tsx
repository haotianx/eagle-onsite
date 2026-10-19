import "@xyflow/react/dist/style.css";

import { Background, Controls, Handle, Position, ReactFlow, type NodeProps, type NodeTypes } from "@xyflow/react";
import { useMemo } from "react";

import type { NodeInfo } from "@/api/types";
import { NodeStateBadge } from "@/components/NodeStateBadge";
import { cn } from "@/lib/utils";

import { layoutDag, NODE_HEIGHT, NODE_WIDTH, type DagFlowNode } from "./graph";

function DagNodeCard({ data }: NodeProps<DagFlowNode>) {
  const { info, selected } = data;
  return (
    <div
      className={cn(
        "flex cursor-pointer flex-col justify-center gap-0.5 rounded-lg border bg-card px-3 shadow-xs",
        selected && "border-ring ring-2 ring-ring/50",
      )}
      style={{ width: NODE_WIDTH, height: NODE_HEIGHT }}
    >
      <Handle type="target" position={Position.Top} isConnectable={false} className="opacity-0" />
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-sm font-medium">{info.name}</span>
        <NodeStateBadge state={info.state} />
      </div>
      <div className="truncate text-xs text-muted-foreground">{info.artifact_type ?? "—"}</div>
      <div className="truncate font-mono text-xs">{info.current?.label ?? "—"}</div>
      <Handle type="source" position={Position.Bottom} isConnectable={false} className="opacity-0" />
    </div>
  );
}

const NODE_TYPES: NodeTypes = { dag: DagNodeCard };

export interface DagGraphProps {
  nodes: NodeInfo[];
  selected?: string;
  onSelect(node: string | undefined): void;
}

/** Read-only DAG graph; clicking a node selects it, clicking the background clears the selection. */
export function DagGraph({ nodes, selected, onSelect }: DagGraphProps) {
  const layout = useMemo(() => layoutDag(nodes, selected), [nodes, selected]);
  return (
    <ReactFlow
      nodes={layout.nodes}
      edges={layout.edges}
      nodeTypes={NODE_TYPES}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      deleteKeyCode={null}
      fitView
      minZoom={0.2}
      onNodeClick={(_, node) => onSelect(node.id)}
      onPaneClick={() => onSelect(undefined)}
    >
      <Background />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}
