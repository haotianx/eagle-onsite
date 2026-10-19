import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import type { NodeInfo, VersionSummary } from "@/api/types";
import { NodeStateBadge } from "@/components/NodeStateBadge";
import { Button } from "@/components/ui/button";
import { VersionLabel, type VersionRole } from "@/components/VersionLabel";
import { cn } from "@/lib/utils";

/** The newest of a node's candidate versions. */
export function newestCandidate(node: NodeInfo): VersionSummary | undefined {
  return node.candidates.reduce<VersionSummary | undefined>((best, v) => (!best || v.id > best.id ? v : best), undefined);
}

function formatParam(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-1.5">
      <h3 className="text-xs font-medium text-muted-foreground">{title}</h3>
      {children}
    </section>
  );
}

function VersionLinks({ versions, role }: { versions: VersionSummary[]; role: VersionRole }) {
  if (versions.length === 0) return <div className="text-sm text-muted-foreground">—</div>;
  return (
    <ul className="space-y-1">
      {versions.map((v) => (
        <li key={v.id}>
          <Link to="/versions/$versionId" params={{ versionId: String(v.id) }} className="hover:underline">
            <VersionLabel version={v} role={role} />
          </Link>
        </li>
      ))}
    </ul>
  );
}

export interface NodePanelProps {
  node: NodeInfo;
  onRun(): void;
  onPromote(versionId: number): void;
  className?: string;
}

export function NodePanel({ node, onRun, onPromote, className }: NodePanelProps) {
  const candidate = newestCandidate(node);
  const params = Object.entries(node.params);
  return (
    <aside className={cn("space-y-4 p-4", className)} aria-label={`Node ${node.name}`}>
      <div className="space-y-1.5">
        <div className="flex items-center gap-2">
          <h2 className="font-semibold">{node.name}</h2>
          <NodeStateBadge state={node.state} />
        </div>
        {node.reasons.length > 0 && (
          <ul className="text-xs text-muted-foreground">
            {node.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={onRun}>
          Run…
        </Button>
        <Button size="sm" variant="outline" disabled={!candidate} onClick={() => candidate && onPromote(candidate.id)}>
          Promote
        </Button>
        {node.current ? (
          <Button size="sm" variant="outline" asChild>
            <Link to="/versions/$versionId" params={{ versionId: String(node.current.id) }}>
              Open artifact
            </Link>
          </Button>
        ) : (
          <Button size="sm" variant="outline" disabled>
            Open artifact
          </Button>
        )}
      </div>

      <Section title="Script">
        <div className="font-mono text-sm break-all">{node.script}</div>
      </Section>

      <Section title="Params">
        {params.length === 0 ? (
          <div className="text-sm text-muted-foreground">—</div>
        ) : (
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 font-mono text-xs">
            {params.map(([key, value]) => (
              <div key={key} className="contents">
                <dt className="text-muted-foreground">{key}</dt>
                <dd className="break-all">{formatParam(value)}</dd>
              </div>
            ))}
          </dl>
        )}
      </Section>

      <Section title="Checks">
        <div className="font-mono text-sm break-all">{node.checks ?? "—"}</div>
      </Section>

      <Section title="Current">
        <VersionLinks versions={node.current ? [node.current] : []} role="current" />
      </Section>
      <Section title="Candidates">
        <VersionLinks versions={node.candidates} role="candidate" />
      </Section>
      <Section title="Scratch">
        <VersionLinks versions={node.scratch} role="scratch" />
      </Section>

      <dl className="grid grid-cols-2 gap-2 text-sm">
        <div>
          <dt className="text-xs text-muted-foreground">Docs</dt>
          <dd className="tabular-nums">{node.doc_count ?? "—"}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Failures</dt>
          <dd className="tabular-nums">{node.failure_count ?? "—"}</dd>
        </div>
      </dl>
    </aside>
  );
}
