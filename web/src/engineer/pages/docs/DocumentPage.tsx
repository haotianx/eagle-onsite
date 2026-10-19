import { getRouteApi, Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";

import { useDag, useNodeVersions, useVersionDoc } from "@/api/hooks";
import type { NodeInfo } from "@/api/types";
import { PdfViewer } from "@/components/pdf/PdfViewer";
import type { Region } from "@/components/pdf/regions";
import { Button } from "@/components/ui/button";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { VersionPicker } from "@/components/VersionPicker";
import { RunLauncher } from "@/engineer/pages/dags/RunLauncher";

import { DocTab } from "./DocTab";

const route = getRouteApi("/dags/$dag/docs/$docId");

/** The version shown for a node with no explicit choice: its current version, else its newest. */
function useDefaultVersion(dag: string, node: NodeInfo | undefined): { id: number | undefined; isPending: boolean } {
  const versions = useNodeVersions(dag, node && !node.current ? node.name : undefined);
  if (node?.current) return { id: node.current.id, isPending: false };
  return { id: versions.data?.[0]?.id, isPending: !!node && versions.isPending };
}

export function DocumentPage() {
  const { dag, docId } = route.useParams();
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const dagDetail = useDag(dag);
  const nodes = useMemo(() => dagDetail.data?.nodes ?? [], [dagDetail.data]);
  const active = nodes.find((n) => n.name === search.node) ?? nodes[0];

  // Each tab's chosen version; the URL holds the active tab's.
  const [choices, setChoices] = useState<Record<string, number>>({});
  const chosen = useMemo(
    () => (search.node && search.version != null ? { ...choices, [search.node]: search.version } : choices),
    [choices, search.node, search.version],
  );
  const defaultVersion = useDefaultVersion(dag, active);
  const versionId = active ? (chosen[active.name] ?? defaultVersion.id) : undefined;

  const [hovered, setHovered] = useState<Region[] | null>(null);
  const [selected, setSelected] = useState<Region[]>([]);
  const [page, setPage] = useState(search.page);
  const [launcherOpen, setLauncherOpen] = useState(false);
  const highlights = hovered ?? selected;

  const clearHighlights = () => {
    setHovered(null);
    setSelected([]);
  };

  const selectTab = (node: string) => {
    setChoices(chosen);
    clearHighlights();
    void navigate({ search: (prev) => ({ ...prev, node, version: chosen[node] }), replace: true });
  };

  const selectVersion = (version: number) => {
    if (!active) return;
    setChoices((c) => ({ ...c, [active.name]: version }));
    clearHighlights();
    void navigate({ search: (prev) => ({ ...prev, node: active.name, version }), replace: true });
  };

  const doc = useVersionDoc(versionId, docId).data;

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-10 shrink-0 items-center gap-2 border-b px-4 text-sm">
        <Link to="/dags/$dag" params={{ dag }} className="text-muted-foreground hover:text-foreground">
          {dag}
        </Link>
        <span className="text-muted-foreground">/</span>
        <span className="truncate">{doc?.filename ?? docId}</span>
        {(doc?.job_number || doc?.project) && (
          <span className="truncate text-xs text-muted-foreground">
            {[doc.job_number, doc.project].filter(Boolean).join(" · ")}
          </span>
        )}
      </div>
      <ResizablePanelGroup className="min-h-0 flex-1">
        <ResizablePanel defaultSize="50" minSize="20">
          <PdfViewer docId={docId} page={page} highlights={highlights} className="h-full" />
        </ResizablePanel>
        <ResizableHandle />
        <ResizablePanel defaultSize="50" minSize="25">
          {dagDetail.isPending ? (
            <Skeleton className="m-3 h-40" />
          ) : dagDetail.isError ? (
            <p className="p-3 text-sm text-destructive">{dagDetail.error.message}</p>
          ) : !active ? (
            <p className="p-3 text-sm text-muted-foreground">No nodes</p>
          ) : (
            <Tabs value={active.name} onValueChange={selectTab} className="h-full gap-0">
              <div className="flex shrink-0 flex-wrap items-center gap-2 border-b px-3 py-2">
                <TabsList>
                  {nodes.map((n) => (
                    <TabsTrigger key={n.name} value={n.name}>
                      {n.name}
                    </TabsTrigger>
                  ))}
                </TabsList>
                <div className="ml-auto flex items-center gap-2">
                  <VersionPicker
                    dag={dag}
                    node={active.name}
                    value={versionId}
                    onChange={selectVersion}
                    className="w-64"
                  />
                  <Button size="sm" variant="outline" onClick={() => setLauncherOpen(true)}>
                    Run…
                  </Button>
                </div>
              </div>
              {nodes.map((n) => (
                <TabsContent key={n.name} value={n.name} className="min-h-0 flex-1 overflow-auto p-3">
                  {versionId == null && defaultVersion.isPending ? (
                    <Skeleton className="h-40 w-full" />
                  ) : versionId == null ? (
                    <p className="text-sm text-muted-foreground">No versions</p>
                  ) : (
                    <DocTab
                      node={n.name}
                      docId={docId}
                      versionId={versionId}
                      onHover={setHovered}
                      onSelect={(regions, p) => {
                        setSelected(regions);
                        if (p != null) setPage(p);
                      }}
                    />
                  )}
                </TabsContent>
              ))}
            </Tabs>
          )}
        </ResizablePanel>
      </ResizablePanelGroup>
      {active && (
        <RunLauncher
          dag={dag}
          node={active.name}
          docIds={[docId]}
          open={launcherOpen}
          onOpenChange={setLauncherOpen}
        />
      )}
    </div>
  );
}
