import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import type { VersionDetail } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { formatDateTime, formatDuration, shortSha } from "@/lib/format";

function text(value: unknown): string {
  if (value == null || value === "") return "—";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

/** Where a version came from: inputs, code, snapshot, params, environment, describe() and timing. */
export function Lineage({ version }: { version: VersionDetail }) {
  const meta = version.meta;
  const describe = (meta.describe ?? {}) as Record<string, unknown>;
  const params = (meta.params ?? {}) as Record<string, unknown>;
  const wall = typeof version.stats.wall_s === "number" ? version.stats.wall_s : undefined;

  const rows: [string, ReactNode][] = [
    [
      "Inputs",
      version.inputs.length === 0 ? (
        "—"
      ) : (
        <div className="flex flex-col gap-1">
          {version.inputs.map((input) => (
            <div key={input.node} className="flex items-center gap-2">
              <Link
                to="/versions/$versionId"
                params={{ versionId: String(input.version.id) }}
                className="font-mono hover:underline"
              >
                {input.version.label}
              </Link>
              {input.current ? (
                <Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-700">
                  Current
                </Badge>
              ) : (
                <Badge variant="outline" className="border-amber-200 bg-amber-50 text-amber-700">
                  Not current
                </Badge>
              )}
            </div>
          ))}
        </div>
      ),
    ],
    [
      "Commit",
      <span className="font-mono">
        {version.commit ? shortSha(version.commit) : "—"}
        {version.dirty && " + dirty"}
      </span>,
    ],
    ["Snapshot", `#${version.snapshot_id}`],
    [
      "Run",
      version.run_id == null ? (
        "—"
      ) : (
        <Link to="/runs/$runId" params={{ runId: String(version.run_id) }} className="hover:underline">
          #{version.run_id}
        </Link>
      ),
    ],
    ["Fingerprint", <span className="font-mono break-all">{version.node_fp}</span>],
    ["Script", <span className="font-mono">{text(meta.script)}</span>],
    ["Script hash", <span className="font-mono">{shortSha(meta.script_hash as string | undefined, 12)}</span>],
    ["Params", <KeyValues values={params} />],
    ["Environment", <span className="font-mono">{shortSha(meta.env as string | undefined, 12)}</span>],
    ["Describe", <KeyValues values={describe} />],
    ["Created", formatDateTime(version.created_at)],
    ["Finalized", formatDateTime(version.finalized_at)],
    ["Wall time", formatDuration(wall)],
  ];

  return (
    <dl className="grid grid-cols-[7rem_1fr] gap-x-4 gap-y-1.5 text-sm">
      {rows.map(([term, value]) => (
        <div key={term} className="contents">
          <dt className="text-muted-foreground">{term}</dt>
          <dd className="min-w-0">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function KeyValues({ values }: { values: Record<string, unknown> }) {
  const entries = Object.entries(values);
  if (entries.length === 0) return <>—</>;
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-3 font-mono text-xs">
      {entries.map(([key, value]) => (
        <div key={key} className="contents">
          <dt className="text-muted-foreground">{key}</dt>
          <dd className="break-all">{text(value)}</dd>
        </div>
      ))}
    </dl>
  );
}
