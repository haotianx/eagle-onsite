import { getRouteApi } from "@tanstack/react-router";
import { SearchIcon } from "lucide-react";
import { useEffect, useState } from "react";

import { useSearch } from "@/api/hooks";
import type { SearchSide } from "@/api/types";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import { EmbedVersionPicker } from "./EmbedVersionPicker";
import { defaultVersions, embedOptions, useEmbedNodes } from "./embedVersions";
import { buildSearchRequest, DEFAULT_K, K_OPTIONS, rankChanges } from "./results";
import { SearchHitRow } from "./SearchHitRow";

const route = getRouteApi("/search");

export function SearchPage() {
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const embed = useEmbedNodes();
  const options = embedOptions(embed.nodes);
  const defaults = defaultVersions(embed.nodes);
  const a = search.a ?? defaults.a;
  const b = search.b ?? defaults.b;
  const k = search.k ?? DEFAULT_K;

  const [text, setText] = useState(search.q ?? "");
  useEffect(() => setText(search.q ?? ""), [search.q]);

  const request = buildSearchRequest(search.q, a, b, k);
  const results = useSearch(request);
  const sides = request ? results.data?.sides : undefined;

  const update = (next: { q?: string; a?: number; b?: number; k?: number }) =>
    void navigate({ search: (prev) => ({ ...prev, a, b, ...next }) });

  const columns = [
    { id: a, set: (id: number) => update({ a: id }), label: "Left version" },
    ...(b == null ? [] : [{ id: b, set: (id: number) => update({ b: id }), label: "Right version" }]),
  ];
  const kOptions = [...new Set<number>([...K_OPTIONS, k])].sort((x, y) => x - y);

  return (
    <div className="space-y-4 p-4">
      <h1 className="text-lg font-semibold">Search</h1>
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          update({ q: text.trim() || undefined });
        }}
      >
        <InputGroup className="flex-1">
          <InputGroupAddon>
            <SearchIcon />
          </InputGroupAddon>
          <InputGroupInput aria-label="Query" value={text} onChange={(e) => setText(e.target.value)} />
        </InputGroup>
        <Select value={String(k)} onValueChange={(v) => update({ k: Number(v) })}>
          <SelectTrigger aria-label="Top k">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {kOptions.map((n) => (
              <SelectItem key={n} value={String(n)}>
                Top {n}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </form>

      {embed.error && <ErrorAlert error={embed.error} />}
      {!embed.isPending && !embed.error && options.length === 0 && (
        <p className="text-sm text-muted-foreground">No embed versions</p>
      )}
      {results.error && request && <ErrorAlert error={results.error} />}

      {a != null && (
        <div className={cn("grid gap-6", columns.length === 2 && "md:grid-cols-2")}>
          {columns.map((column, i) => (
            <section key={i} className="min-w-0 space-y-2">
              <div className="flex items-center gap-2">
                <EmbedVersionPicker
                  options={options}
                  value={column.id}
                  onChange={column.set}
                  isPending={embed.isPending}
                  label={column.label}
                />
                {sides?.[i]?.model && (
                  <span className="truncate font-mono text-xs text-muted-foreground">{sides[i].model}</span>
                )}
              </div>
              {request && (
                <SideResults
                  side={sides?.[i]}
                  other={i === 1 ? sides?.[0] : undefined}
                  loading={results.isPending && !results.error}
                  stale={results.isPlaceholderData}
                />
              )}
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

interface SideResultsProps {
  side: SearchSide | undefined;
  /** The side rank changes are shown against. */
  other: SearchSide | undefined;
  loading: boolean;
  stale: boolean;
}

function SideResults({ side, other, loading, stale }: SideResultsProps) {
  if (loading) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-20" />
        ))}
      </div>
    );
  }
  if (!side) return null;
  if (side.hits.length === 0) return <p className="text-sm text-muted-foreground">No results</p>;
  const changes = other ? rankChanges(side.hits, other.hits) : undefined;
  return (
    <ol className={cn(stale && "opacity-60")}>
      {side.hits.map((hit, i) => (
        <SearchHitRow key={hit.chunk_id || i} hit={hit} version={side.version} change={changes?.[i]} />
      ))}
    </ol>
  );
}

function ErrorAlert({ error }: { error: Error }) {
  return (
    <Alert variant="destructive">
      <AlertDescription>{error.message}</AlertDescription>
    </Alert>
  );
}
