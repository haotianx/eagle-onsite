import { Link } from "@tanstack/react-router";
import { ChevronRightIcon } from "lucide-react";
import { useState } from "react";

import { useVersionDoc, useVersionDocs } from "@/api/hooks";
import type { VersionDetail, VersionDocOut } from "@/api/types";
import { DocName } from "@/components/DocName";
import { VersionLabel } from "@/components/VersionLabel";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Skeleton } from "@/components/ui/skeleton";

/** Failed docs of each version the run produced. */
export function RunErrors({ versions }: { versions: VersionDetail[] }) {
  const failing = versions.filter((v) => v.failed > 0);
  if (failing.length === 0) return <p className="text-sm text-muted-foreground">No errors</p>;
  return (
    <div className="flex flex-col gap-4">
      {failing.map((v) => (
        <VersionErrors key={v.id} version={v} />
      ))}
    </div>
  );
}

function VersionErrors({ version }: { version: VersionDetail }) {
  const docs = useVersionDocs(version.id, { status: "error" });
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <Link to="/versions/$versionId" params={{ versionId: String(version.id) }} className="hover:underline">
          <VersionLabel version={version} />
        </Link>
        <span className="text-xs text-destructive">{version.failed} failed</span>
      </div>
      {docs.isPending ? (
        <Skeleton className="h-10 w-full" />
      ) : docs.isError ? (
        <p className="text-sm text-destructive">{docs.error.message}</p>
      ) : (
        <ul className="divide-y rounded-md border">
          {docs.data.items.map((doc) => (
            <FailedDoc key={doc.doc_id} version={version} doc={doc} />
          ))}
        </ul>
      )}
    </div>
  );
}

function FailedDoc({ version, doc }: { version: VersionDetail; doc: VersionDocOut }) {
  const [open, setOpen] = useState(false);
  return (
    <li>
      <Collapsible open={open} onOpenChange={setOpen}>
        <div className="flex items-start gap-2 px-2 py-1.5 text-sm">
          <CollapsibleTrigger
            aria-label={`Traceback for ${doc.filename ?? doc.doc_id}`}
            className="mt-0.5 rounded p-0.5 hover:bg-muted"
          >
            <ChevronRightIcon className={`size-4 transition-transform ${open ? "rotate-90" : ""}`} />
          </CollapsibleTrigger>
          <Link
            to="/dags/$dag/docs/$docId"
            params={{ dag: version.dag, docId: doc.doc_id }}
            search={{ node: version.node, version: version.id }}
            className="w-64 shrink-0 hover:underline"
          >
            <DocName doc={doc} />
          </Link>
          <div className="min-w-0 flex-1">
            <span className="font-mono text-destructive">{doc.error_type}</span>
            {doc.error_message && <span className="ml-2 break-words">{doc.error_message}</span>}
          </div>
        </div>
        <CollapsibleContent>{open && <Traceback versionId={version.id} docId={doc.doc_id} />}</CollapsibleContent>
      </Collapsible>
    </li>
  );
}

function Traceback({ versionId, docId }: { versionId: number; docId: string }) {
  const detail = useVersionDoc(versionId, docId);
  if (detail.isPending) return <Skeleton className="mx-2 mb-2 h-24" />;
  if (detail.isError) return <p className="px-2 pb-2 text-sm text-destructive">{detail.error.message}</p>;
  return (
    <pre className="mx-2 mb-2 max-h-96 overflow-auto rounded bg-muted p-2 font-mono text-xs whitespace-pre">
      {detail.data.traceback ?? "No traceback"}
    </pre>
  );
}
