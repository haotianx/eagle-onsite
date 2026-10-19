import { ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";

import type { Region } from "@/components/pdf/regions";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cn } from "@/lib/utils";

import { chunkPage, chunkRows, isChunkRecord } from "./chunks";
import { isParseRecord, parseRecord, type ParseElement } from "./docling";

export interface RegionHandlers {
  onHover(regions: Region[] | null): void;
  onSelect(regions: Region[], page: number | undefined): void;
}

export interface RecordViewProps extends RegionHandlers {
  artifactType: string | null;
  record: unknown;
  /** Embedding dimension (embed versions). */
  vectorDim?: number;
}

/** A doc's record, viewed by artifact type. */
export function RecordView({ artifactType, record, vectorDim, onHover, onSelect }: RecordViewProps) {
  if (artifactType === "parse" && isParseRecord(record))
    return <ParseView record={record} onHover={onHover} onSelect={onSelect} />;
  if ((artifactType === "chunk" || artifactType === "embed") && isChunkRecord(record))
    return (
      <ChunkView rows={record} vectorDim={artifactType === "embed" ? vectorDim : undefined} onHover={onHover} onSelect={onSelect} />
    );
  return <JsonTree value={record} />;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-1.5">
      <h3 className="text-xs font-medium text-muted-foreground">{title}</h3>
      {children}
    </section>
  );
}

function ParseView({ record, ...handlers }: { record: Parameters<typeof parseRecord>[0] } & RegionHandlers) {
  const { pages, elements } = useMemo(() => parseRecord(record), [record]);
  return (
    <div className="space-y-4">
      <Section title={`Pages (${pages.length})`}>
        <Table className="text-xs">
          <TableHeader>
            <TableRow>
              <TableHead className="w-14">Page</TableHead>
              <TableHead>Size</TableHead>
              <TableHead className="text-right">Text chars</TableHead>
              <TableHead className="w-14 text-right">OCR</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {pages.map((p) => (
              <TableRow key={p.page} className="cursor-pointer" onClick={() => handlers.onSelect([], p.page)}>
                <TableCell className="tabular-nums">{p.page}</TableCell>
                <TableCell className="tabular-nums">
                  {Math.round(p.width)} × {Math.round(p.height)}
                </TableCell>
                <TableCell className="text-right tabular-nums">{p.text_chars.toLocaleString()}</TableCell>
                <TableCell className="text-right">{p.ocr ? "Yes" : ""}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Section>
      <Section title={`Elements (${elements.length})`}>
        <ul className="divide-y rounded-md border text-xs">
          {elements.map((el) => (
            <RegionItem key={el.ref} regions={el.regions} page={el.page} {...handlers}>
              <div className="flex items-baseline gap-2">
                <span className="w-28 shrink-0 font-mono text-muted-foreground">{el.label}</span>
                <div className="min-w-0 flex-1">
                  {el.cells ? <Cells element={el} /> : <p className="whitespace-pre-wrap break-words">{el.text}</p>}
                </div>
                <span className="shrink-0 tabular-nums text-muted-foreground">{el.page}</span>
              </div>
            </RegionItem>
          ))}
        </ul>
      </Section>
    </div>
  );
}

const MAX_TABLE_CELLS = 400;

function Cells({ element }: { element: ParseElement }) {
  const cells = element.cells ?? [];
  const count = cells.reduce((n, row) => n + row.length, 0);
  if (count === 0) return null;
  if (count > MAX_TABLE_CELLS)
    return (
      <span className="text-muted-foreground">
        {cells.length} × {cells[0]?.length ?? 0}
      </span>
    );
  return (
    <table className="border-collapse">
      <tbody>
        {cells.map((row, i) => (
          <tr key={i}>
            {row.map((cell, j) => (
              <td key={j} className="border px-1 py-0.5 align-top">
                {cell}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ChunkView({ rows, vectorDim, ...handlers }: { rows: Record<string, unknown>[]; vectorDim?: number } & RegionHandlers) {
  const chunks = useMemo(() => chunkRows(rows), [rows]);
  const title = [`Chunks (${chunks.length})`, vectorDim != null && `${vectorDim} dims`].filter(Boolean).join(" · ");
  return (
    <Section title={title}>
      <ul className="divide-y rounded-md border text-xs">
        {chunks.map((chunk) => (
          <RegionItem key={chunk.chunk_id} regions={chunk.regions} page={chunkPage(chunk)} {...handlers}>
            <div className="mb-1 flex items-baseline gap-2 text-muted-foreground">
              <span className="font-mono">#{chunk.seq}</span>
              <span className="min-w-0 flex-1 truncate">{chunk.section.join(" › ")}</span>
              <span className="shrink-0 tabular-nums">{pagesLabel(chunk.pages)}</span>
              {chunk.tokens != null && <span className="shrink-0 tabular-nums">{chunk.tokens} tok</span>}
            </div>
            <p className="whitespace-pre-wrap break-words">{chunk.text}</p>
          </RegionItem>
        ))}
      </ul>
    </Section>
  );
}

function pagesLabel(pages: number[]): string {
  if (pages.length === 0) return "";
  const lo = Math.min(...pages);
  const hi = Math.max(...pages);
  return lo === hi ? `p. ${lo}` : `p. ${lo}–${hi}`;
}

function RegionItem({
  regions,
  page,
  onHover,
  onSelect,
  children,
}: { regions: Region[]; page: number | undefined; children: React.ReactNode } & RegionHandlers) {
  return (
    <li
      className="cursor-pointer px-2 py-1.5 hover:bg-muted/60"
      onMouseEnter={() => onHover(regions.length ? regions : null)}
      onMouseLeave={() => onHover(null)}
      onClick={() => onSelect(regions, page)}
    >
      {children}
    </li>
  );
}

/** Collapsible JSON; children render only when expanded. */
export function JsonTree({ value }: { value: unknown }) {
  return (
    <div className="font-mono text-xs">
      <JsonNode name={null} value={value} defaultOpen />
    </div>
  );
}

function JsonNode({ name, value, defaultOpen = false }: { name: string | null; value: unknown; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const key = name != null && <span className="text-muted-foreground">{name}: </span>;
  if (value === null || typeof value !== "object")
    return (
      <div className="pl-4 break-all whitespace-pre-wrap">
        {key}
        <span className={cn(typeof value === "string" ? "text-emerald-700" : "text-sky-700")}>{JSON.stringify(value)}</span>
      </div>
    );
  const entries = Array.isArray(value) ? value.map((v, i) => [String(i), v] as const) : Object.entries(value);
  const brackets = Array.isArray(value) ? ["[", "]"] : ["{", "}"];
  return (
    <div>
      <button type="button" className="flex items-center hover:bg-muted/60" onClick={() => setOpen((o) => !o)}>
        <ChevronRight className={cn("size-3.5 shrink-0 transition-transform", open && "rotate-90")} />
        {key}
        <span className="text-muted-foreground">
          {brackets[0]}
          {open ? "" : `${entries.length}${brackets[1]}`}
        </span>
      </button>
      {open && (
        <div className="ml-1.5 border-l pl-1">
          {entries.map(([k, v]) => (
            <JsonNode key={k} name={k} value={v} />
          ))}
          <div className="pl-4 text-muted-foreground">{brackets[1]}</div>
        </div>
      )}
    </div>
  );
}
