import { getRouteApi, Link } from "@tanstack/react-router";
import { useState } from "react";
import { toast } from "sonner";

import { useRevalidate, useVersion } from "@/api/hooks";
import type { VersionDetail } from "@/api/types";
import { ScratchBadge } from "@/components/ScratchBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { VersionLabel } from "@/components/VersionLabel";
import { VersionPicker } from "@/components/VersionPicker";

import { Lineage } from "./Lineage";
import { MetricCards } from "./MetricCards";
import { PromoteButton } from "./PromoteButton";
import { VersionDocs } from "./VersionDocs";
import { VersionIssues } from "./VersionIssues";

const route = getRouteApi("/versions/$versionId");

export function VersionPage() {
  const { versionId } = route.useParams();
  const id = Number(versionId);
  const version = useVersion(Number.isInteger(id) ? id : undefined);

  if (version.isError) return <p className="p-4 text-sm text-destructive">{version.error.message}</p>;
  if (!version.data) {
    return (
      <div className="flex flex-col gap-3 p-4">
        <Skeleton className="h-7 w-80" />
        <Skeleton className="h-40" />
      </div>
    );
  }
  return <VersionView version={version.data} />;
}

function VersionView({ version }: { version: VersionDetail }) {
  const { tab = "docs" } = route.useSearch();
  const navigate = route.useNavigate();

  return (
    <div className="flex flex-col gap-4 p-4">
      <header className="flex flex-wrap items-center gap-2">
        <Link
          to="/dags/$dag"
          params={{ dag: version.dag }}
          search={{ node: version.node }}
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          {version.dag}
        </Link>
        <span className="text-muted-foreground">/</span>
        <VersionLabel version={version} role={version.current ? "current" : "other"} />
        <StatusBadge status={version.status} />
        <ScratchBadge scratch={version.scratch} />
        <div className="ml-auto flex items-center gap-2">
          <RevalidateButton version={version} />
          <CompareWith version={version} />
          <PromoteButton version={version} />
        </div>
      </header>

      <MetricCards artifactType={version.artifact_type} summary={version.summary} />

      <section className="flex flex-col gap-2">
        <h2 className="text-sm font-semibold">Lineage</h2>
        <Lineage version={version} />
      </section>

      <Tabs
        value={tab}
        onValueChange={(value) =>
          void navigate({ search: (prev) => ({ ...prev, tab: value === "docs" ? undefined : (value as "issues") }) })
        }
      >
        <TabsList>
          <TabsTrigger value="docs">Documents</TabsTrigger>
          <TabsTrigger value="issues">
            Issues
            {version.issues.error + version.issues.warning > 0 && (
              <span className="text-xs text-muted-foreground tabular-nums">
                {version.issues.error + version.issues.warning}
              </span>
            )}
          </TabsTrigger>
        </TabsList>
        <TabsContent value="docs">
          <VersionDocs version={version} />
        </TabsContent>
        <TabsContent value="issues">
          <VersionIssues version={version} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function StatusBadge({ status }: { status: VersionDetail["status"] }) {
  if (status === "complete") return null;
  return (
    <Badge variant="outline" className={status === "building" ? "border-violet-200 bg-violet-50 text-violet-700" : ""}>
      {status === "building" ? "Building" : "Abandoned"}
    </Badge>
  );
}

function RevalidateButton({ version }: { version: VersionDetail }) {
  const revalidate = useRevalidate();
  return (
    <Button
      size="sm"
      variant="outline"
      disabled={revalidate.isPending}
      onClick={() =>
        revalidate.mutate(version.id, {
          onSuccess: (result) => toast.success(`Revalidated: ${result.issues} ${result.issues === 1 ? "issue" : "issues"}`),
          onError: (error) => toast.error(error.message),
        })
      }
    >
      {version.checks_changed && <span className="size-1.5 rounded-full bg-amber-500" aria-hidden />}
      Revalidate
      {version.checks_changed && <span className="text-xs text-amber-700">Checks changed</span>}
    </Button>
  );
}

function CompareWith({ version }: { version: VersionDetail }) {
  const [open, setOpen] = useState(false);
  const navigate = route.useNavigate();
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button size="sm" variant="outline">
          Compare with…
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80">
        <VersionPicker
          dag={version.dag}
          node={version.node}
          value={undefined}
          className="w-full"
          onChange={(other) => {
            setOpen(false);
            void navigate({ to: "/compare", search: { a: other, b: version.id } });
          }}
        />
      </PopoverContent>
    </Popover>
  );
}
