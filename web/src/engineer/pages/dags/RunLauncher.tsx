import { useNavigate } from "@tanstack/react-router";
import { useMemo, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { useDag, usePlanRun, useSources, useStartRun } from "@/api/hooks";
import { ScratchBadge } from "@/components/ScratchBadge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { VersionPicker } from "@/components/VersionPicker";
import { formatDateTime } from "@/lib/format";

import { DocPicker } from "./DocPicker";
import {
  buildRunRequest,
  externalInputs,
  initialLauncherState,
  shownCode,
  type Code,
  type DocsMode,
  type LauncherState,
  type Selection,
} from "./launcher";
import { useDebouncedValue } from "./useDebouncedValue";

export interface RunLauncherProps {
  dag: string;
  node?: string;
  docIds?: string[];
  open: boolean;
  onOpenChange(open: boolean): void;
}

const PLAN_DEBOUNCE_MS = 300;

const SELECTIONS: { value: Selection; label: string; needsNode: boolean }[] = [
  { value: "node", label: "node", needsNode: true },
  { value: "downstream", label: "+ downstream", needsNode: true },
  { value: "upstream", label: "+ upstream", needsNode: true },
  { value: "all", label: "all", needsNode: false },
];

/** Run launcher drawer, preselected from where it's opened. */
export function RunLauncher({ dag, node, docIds, open, onOpenChange }: RunLauncherProps) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-lg" aria-describedby={undefined}>
        <SheetHeader>
          <SheetTitle>Run {dag}</SheetTitle>
        </SheetHeader>
        {open && <LauncherForm dag={dag} node={node} docIds={docIds} onDone={() => onOpenChange(false)} />}
      </SheetContent>
    </Sheet>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      {children}
    </div>
  );
}

function LauncherForm({
  dag,
  node,
  docIds,
  onDone,
}: {
  dag: string;
  node?: string;
  docIds?: string[];
  onDone(): void;
}) {
  const [state, setState] = useState<LauncherState>(() => initialLauncherState(node, docIds));
  const update = (patch: Partial<LauncherState>) => setState((s) => ({ ...s, ...patch }));

  const request = useMemo(() => buildRunRequest(dag, node, state), [dag, node, state]);
  const planRequest = useDebouncedValue(request, PLAN_DEBOUNCE_MS);
  const plan = usePlanRun(planRequest);
  const planData = plan.isError ? undefined : plan.data;

  const dagDetail = useDag(dag);
  const sources = useSources();
  const snapshots = sources.data?.find((s) => s.name === dagDetail.data?.source)?.snapshots ?? [];
  const latest = snapshots[0];

  const start = useStartRun();
  const navigate = useNavigate();
  const startRun = () => {
    if (!request) return;
    start.mutate(request, {
      onSuccess: ({ run_id }) => {
        toast.success(`Run #${run_id} started`);
        onDone();
        void navigate({ to: "/runs/$runId", params: { runId: String(run_id) } });
      },
    });
  };

  const inputs = planData ? externalInputs(planData) : [];
  const code = shownCode(state.code, planData);

  return (
    <>
      <div className="space-y-4 px-4">
        <Field label="Nodes">
          <ToggleGroup
            type="single"
            variant="outline"
            size="sm"
            spacing={0}
            aria-label="Nodes"
            value={state.selection}
            onValueChange={(v) => v && update({ selection: v as Selection, inputVersions: {} })}
          >
            {SELECTIONS.map((s) => (
              <ToggleGroupItem key={s.value} value={s.value} disabled={s.needsNode && !node}>
                {s.label}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
          {planData && (
            <div className="text-xs text-muted-foreground">{planData.nodes.map((n) => n.node).join(", ")}</div>
          )}
        </Field>

        <Field label="Snapshot">
          <Select
            value={state.snapshotId == null ? "latest" : String(state.snapshotId)}
            onValueChange={(v) => update({ snapshotId: v === "latest" ? null : Number(v) })}
          >
            <SelectTrigger size="sm" className="w-full" aria-label="Snapshot">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="latest">Latest{latest ? ` (#${latest.id})` : ""}</SelectItem>
              {snapshots.map((s) => (
                <SelectItem key={s.id} value={String(s.id)}>
                  #{s.id} · {formatDateTime(s.created_at)} · {s.doc_count} docs
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>

        {inputs.length > 0 && (
          <Field label="Input versions">
            <div className="space-y-1.5">
              {inputs.map(({ node: input, version }) => (
                <div key={input} className="flex items-center gap-2">
                  <span className="w-24 shrink-0 truncate text-sm">{input}</span>
                  <VersionPicker
                    dag={dag}
                    node={input}
                    value={state.inputVersions[input] ?? version.id}
                    onChange={(id) => update({ inputVersions: { ...state.inputVersions, [input]: id } })}
                    className="min-w-0 flex-1"
                  />
                </div>
              ))}
            </div>
          </Field>
        )}

        <Field label="Recompute">
          <ToggleGroup
            type="single"
            variant="outline"
            size="sm"
            spacing={0}
            aria-label="Recompute"
            value={state.recompute}
            onValueChange={(v) => v && update({ recompute: v as LauncherState["recompute"] })}
          >
            <ToggleGroupItem value="reuse">reuse</ToggleGroupItem>
            <ToggleGroupItem value="force">force</ToggleGroupItem>
          </ToggleGroup>
        </Field>

        <Field label="Documents">
          <ToggleGroup
            type="single"
            variant="outline"
            size="sm"
            spacing={0}
            aria-label="Documents"
            value={state.docsMode}
            onValueChange={(v) => v && update({ docsMode: v as DocsMode })}
          >
            <ToggleGroupItem value="all">all</ToggleGroupItem>
            <ToggleGroupItem value="sample">sample</ToggleGroupItem>
            <ToggleGroupItem value="pick">pick</ToggleGroupItem>
          </ToggleGroup>
          {state.docsMode === "sample" && (
            <Input
              type="number"
              min={1}
              aria-label="Sample size"
              className="h-7 w-24"
              value={Number.isNaN(state.sampleSize) ? "" : state.sampleSize}
              onChange={(e) => update({ sampleSize: e.target.valueAsNumber })}
            />
          )}
          {state.docsMode === "pick" && (
            <DocPicker
              snapshotId={state.snapshotId ?? planData?.snapshot_id ?? latest?.id}
              value={state.picked}
              onChange={(picked) => update({ picked })}
            />
          )}
        </Field>

        <Field label="Code">
          <ToggleGroup
            type="single"
            variant="outline"
            size="sm"
            spacing={0}
            aria-label="Code"
            value={code ?? ""}
            onValueChange={(v) => v && update({ code: v as Code })}
          >
            <ToggleGroupItem value="committed">committed</ToggleGroupItem>
            <ToggleGroupItem value="working">working tree</ToggleGroupItem>
          </ToggleGroup>
        </Field>

        {plan.isError ? (
          <Alert variant="destructive">
            <AlertDescription>{plan.error.message}</AlertDescription>
          </Alert>
        ) : (
          request &&
          planData && (
            <section aria-label="Plan" className="space-y-2 rounded-lg border p-3">
              <div className="flex flex-wrap items-center gap-1.5">
                <ScratchBadge scratch={planData.scratch} />
                {planData.scratch_reasons.map((reason) => (
                  <Badge key={reason} variant="outline" className="text-muted-foreground">
                    {reason}
                  </Badge>
                ))}
                <span className="ml-auto text-xs text-muted-foreground tabular-nums">{planData.doc_count} docs</span>
              </div>
              <ul className="space-y-1 text-sm">
                {planData.nodes.map((n) => (
                  <li key={n.node} className="flex justify-between gap-3">
                    <span className="font-medium">{n.node}</span>
                    <span className="text-muted-foreground tabular-nums">
                      {n.docs_to_process} to process · {n.docs_cached} cached
                      {n.docs_blocked > 0 && ` · ${n.docs_blocked} blocked`}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )
        )}

        {start.isError && (
          <Alert variant="destructive">
            <AlertDescription>{start.error.message}</AlertDescription>
          </Alert>
        )}
      </div>
      <SheetFooter>
        <Button onClick={startRun} disabled={!request || plan.isError || start.isPending}>
          Start run
        </Button>
      </SheetFooter>
    </>
  );
}
