import { Link } from "@tanstack/react-router";
import { useLayoutEffect, useRef, useState } from "react";

import type { SearchHit, VersionSummary } from "@/api/types";
import { DocName } from "@/components/DocName";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

import { firstPage, formatPages, type RankChange } from "./results";

export interface SearchHitRowProps {
  hit: SearchHit;
  version: VersionSummary;
  change?: RankChange;
}

export function SearchHitRow({ hit, version, change }: SearchHitRowProps) {
  const [expanded, setExpanded] = useState(false);
  const [clamped, setClamped] = useState(false);
  const textRef = useRef<HTMLParagraphElement>(null);
  useLayoutEffect(() => {
    const el = textRef.current;
    if (el && !expanded) setClamped(el.scrollHeight > el.clientHeight);
  }, [hit.text, expanded]);
  return (
    <li className="flex gap-3 border-b py-3 last:border-b-0">
      <div className="flex w-12 shrink-0 flex-col items-center gap-1">
        <span className="text-2xl font-semibold tabular-nums leading-none">{hit.rank}</span>
        {change && <RankChangeBadge change={change} />}
      </div>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-start gap-2">
          <Link
            to="/dags/$dag/docs/$docId"
            params={{ dag: version.dag, docId: hit.doc_id }}
            search={{ node: version.node, version: version.id, page: firstPage(hit) }}
            className="min-w-0 flex-1 text-sm font-medium hover:underline"
          >
            <DocName doc={hit} />
          </Link>
          <span className="shrink-0 font-mono text-xs text-muted-foreground" title="Score">
            {hit.score.toFixed(3)}
          </span>
        </div>
        <div className="flex flex-wrap gap-x-2 text-xs text-muted-foreground">
          {hit.pages.length > 0 && <span>{formatPages(hit.pages)}</span>}
          {hit.section.length > 0 && <span className="min-w-0 truncate">{hit.section.join(" › ")}</span>}
        </div>
        <p ref={textRef} className={cn("text-sm whitespace-pre-line", !expanded && "line-clamp-3")}>
          {hit.text}
        </p>
        {(clamped || expanded) && (
          <button
            type="button"
            className="text-xs text-muted-foreground hover:text-foreground"
            onClick={() => setExpanded((e) => !e)}
          >
            {expanded ? "Less" : "More"}
          </button>
        )}
      </div>
    </li>
  );
}

function RankChangeBadge({ change }: { change: RankChange }) {
  if (change.kind === "same") return null;
  if (change.kind === "new") {
    return (
      <Badge variant="outline" className="border-sky-200 bg-sky-50 px-1.5 text-sky-700">
        new
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="px-1.5 font-normal whitespace-nowrap text-muted-foreground">
      was {change.rank}
    </Badge>
  );
}
