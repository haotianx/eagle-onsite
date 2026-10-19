/** Search-param validators for the engineer routes. The router JSON-parses values, so numbers can
 * arrive as numbers or strings and numeric-looking strings as numbers. Invalid values become
 * undefined; every key is returned so the validated value overrides the raw one (route search
 * is merged over the parent's raw search). */

type Raw = Record<string, unknown>;

function str(value: unknown): string | undefined {
  if (typeof value === "string") return value === "" ? undefined : value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return undefined;
}

function int(value: unknown): number | undefined {
  const n = typeof value === "string" && value.trim() !== "" ? Number(value) : value;
  return typeof n === "number" && Number.isInteger(n) ? n : undefined;
}

function oneOf<T extends string>(options: readonly T[], value: unknown): T | undefined {
  return options.includes(value as T) ? (value as T) : undefined;
}

export interface DagSearch {
  node?: string;
}

export function validateDagSearch(raw: Raw): DagSearch {
  return { node: str(raw.node) };
}

export interface DocumentSearch {
  node?: string;
  version?: number;
  page?: number;
}

export function validateDocumentSearch(raw: Raw): DocumentSearch {
  return { node: str(raw.node), version: int(raw.version), page: int(raw.page) };
}

export interface RunsSearch {
  dag?: string;
}

export function validateRunsSearch(raw: Raw): RunsSearch {
  return { dag: str(raw.dag) };
}

export interface RunSearch {
  node?: string;
  doc?: string;
  level?: string;
}

export function validateRunSearch(raw: Raw): RunSearch {
  return { node: str(raw.node), doc: str(raw.doc), level: str(raw.level) };
}

export interface VersionSearch {
  tab?: "docs" | "issues";
  status?: "ok" | "error" | "skipped";
  q?: string;
  level?: "error" | "warning";
}

export function validateVersionSearch(raw: Raw): VersionSearch {
  return {
    tab: oneOf(["docs", "issues"], raw.tab),
    status: oneOf(["ok", "error", "skipped"], raw.status),
    q: str(raw.q),
    level: oneOf(["error", "warning"], raw.level),
  };
}

export interface CompareSearch {
  a?: number;
  b?: number;
  doc?: string;
}

export function validateCompareSearch(raw: Raw): CompareSearch {
  return { a: int(raw.a), b: int(raw.b), doc: str(raw.doc) };
}

export interface SearchSearch {
  q?: string;
  a?: number;
  b?: number;
  k?: number;
}

export function validateSearchSearch(raw: Raw): SearchSearch {
  return { q: str(raw.q), a: int(raw.a), b: int(raw.b), k: int(raw.k) };
}

export interface SourcesSearch {
  source?: string;
  snapshot?: number;
}

export function validateSourcesSearch(raw: Raw): SourcesSearch {
  return { source: str(raw.source), snapshot: int(raw.snapshot) };
}
