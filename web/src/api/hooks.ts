import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "./client";
import type { paths } from "./schema";
import type { RunRequestIn, SearchIn } from "./types";

type Query<P extends keyof paths> = paths[P] extends { get: { parameters: { query?: infer Q } } }
  ? NonNullable<Q>
  : never;

export type VersionDocsQuery = Query<"/api/versions/{version_id}/docs">;
export type VersionIssuesQuery = Query<"/api/versions/{version_id}/issues">;
export type RunLogsQuery = Query<"/api/runs/{run_id}/logs">;

/** Query keys. Each key starts with its resource, so invalidating a prefix covers everything below it. */
export const queryKeys = {
  dags: () => ["dags"] as const,
  dag: (dag: string) => ["dags", dag] as const,
  nodeVersions: (dag: string, node: string) => ["dags", dag, "nodes", node, "versions"] as const,
  runs: (dag?: string) => ["runs", "list", { dag }] as const,
  run: (runId: number) => ["runs", runId] as const,
  runLogs: (runId: number, query: RunLogsQuery = {}) => ["runs", runId, "logs", query] as const,
  runMetrics: (runId: number) => ["runs", runId, "metrics"] as const,
  version: (versionId: number) => ["versions", versionId] as const,
  versionDocs: (versionId: number, query: VersionDocsQuery = {}) =>
    ["versions", versionId, "docs", "list", query] as const,
  versionDoc: (versionId: number, docId: string) => ["versions", versionId, "docs", docId] as const,
  debugFiles: (versionId: number, docId: string) => ["versions", versionId, "docs", docId, "debug"] as const,
  versionIssues: (versionId: number, query: VersionIssuesQuery = {}) =>
    ["versions", versionId, "issues", query] as const,
  plan: (request: RunRequestIn) => ["plan", request] as const,
  compare: (a: number, b: number) => ["compare", a, b] as const,
  compareDoc: (a: number, b: number, docId: string) => ["compare", a, b, "docs", docId] as const,
  search: (body: SearchIn) => ["search", body] as const,
  sources: () => ["sources"] as const,
  snapshotDiff: (snapshotId: number, against?: number) => ["snapshots", snapshotId, "diff", { against }] as const,
  snapshotDocs: (snapshotId: number, q?: string) => ["snapshots", snapshotId, "docs", { q }] as const,
};

// DAGs

export function useDags() {
  return useQuery({ queryKey: queryKeys.dags(), queryFn: () => unwrap(api.GET("/api/dags")) });
}

export function useDag(dag: string | undefined) {
  return useQuery({
    queryKey: queryKeys.dag(dag ?? ""),
    queryFn: () => unwrap(api.GET("/api/dags/{dag}", { params: { path: { dag: dag! } } })),
    enabled: !!dag,
  });
}

export function useNodeVersions(dag: string | undefined, node: string | undefined) {
  return useQuery({
    queryKey: queryKeys.nodeVersions(dag ?? "", node ?? ""),
    queryFn: () =>
      unwrap(api.GET("/api/dags/{dag}/nodes/{node}/versions", { params: { path: { dag: dag!, node: node! } } })),
    enabled: !!dag && !!node,
  });
}

// Runs

/** The plan for a run request; disabled while `request` is null. Keeps the last plan while the next loads. */
export function usePlanRun(request: RunRequestIn | null) {
  return useQuery({
    queryKey: queryKeys.plan(request!),
    queryFn: () => unwrap(api.POST("/api/runs/plan", { body: request! })),
    enabled: request != null,
    placeholderData: keepPreviousData,
  });
}

export function useRuns(dag?: string) {
  return useQuery({
    queryKey: queryKeys.runs(dag),
    queryFn: () => unwrap(api.GET("/api/runs", { params: { query: dag ? { dag } : {} } })),
  });
}

export function useRun(runId: number | undefined) {
  return useQuery({
    queryKey: queryKeys.run(runId ?? -1),
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}", { params: { path: { run_id: runId! } } })),
    enabled: runId != null,
  });
}

export function useRunLogs(runId: number | undefined, query: RunLogsQuery = {}) {
  return useQuery({
    queryKey: queryKeys.runLogs(runId ?? -1, query),
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/logs", { params: { path: { run_id: runId! }, query } })),
    enabled: runId != null,
    placeholderData: keepPreviousData,
  });
}

export function useRunMetrics(runId: number | undefined) {
  return useQuery({
    queryKey: queryKeys.runMetrics(runId ?? -1),
    queryFn: () => unwrap(api.GET("/api/runs/{run_id}/metrics", { params: { path: { run_id: runId! } } })),
    enabled: runId != null,
  });
}

export function useStartRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (request: RunRequestIn) => unwrap(api.POST("/api/runs", { body: request })),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ["runs"] });
      void client.invalidateQueries({ queryKey: queryKeys.dags() });
    },
  });
}

export function useCancelRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (runId: number) =>
      unwrap(api.POST("/api/runs/{run_id}/cancel", { params: { path: { run_id: runId } } })),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ["runs"] });
      void client.invalidateQueries({ queryKey: queryKeys.dags() });
    },
  });
}

// Versions

export function useVersion(versionId: number | undefined) {
  return useQuery({
    queryKey: queryKeys.version(versionId ?? -1),
    queryFn: () =>
      unwrap(api.GET("/api/versions/{version_id}", { params: { path: { version_id: versionId! } } })),
    enabled: versionId != null,
  });
}

export function useVersionDocs(versionId: number | undefined, query: VersionDocsQuery = {}) {
  return useQuery({
    queryKey: queryKeys.versionDocs(versionId ?? -1, query),
    queryFn: () =>
      unwrap(api.GET("/api/versions/{version_id}/docs", { params: { path: { version_id: versionId! }, query } })),
    enabled: versionId != null,
    placeholderData: keepPreviousData,
  });
}

export function useVersionDoc(versionId: number | undefined, docId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.versionDoc(versionId ?? -1, docId ?? ""),
    queryFn: () =>
      unwrap(
        api.GET("/api/versions/{version_id}/docs/{doc_id}", {
          params: { path: { version_id: versionId!, doc_id: docId! } },
        }),
      ),
    enabled: versionId != null && !!docId,
  });
}

export function useDebugFiles(versionId: number | undefined, docId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.debugFiles(versionId ?? -1, docId ?? ""),
    queryFn: () =>
      unwrap(
        api.GET("/api/versions/{version_id}/docs/{doc_id}/debug", {
          params: { path: { version_id: versionId!, doc_id: docId! } },
        }),
      ),
    enabled: versionId != null && !!docId,
  });
}

/** URL of one debug file, for links and downloads. */
export function debugFileUrl(versionId: number, docId: string, name: string): string {
  return `/api/versions/${versionId}/docs/${encodeURIComponent(docId)}/debug/${encodeURIComponent(name)}`;
}

export function useVersionIssues(versionId: number | undefined, query: VersionIssuesQuery = {}) {
  return useQuery({
    queryKey: queryKeys.versionIssues(versionId ?? -1, query),
    queryFn: () =>
      unwrap(api.GET("/api/versions/{version_id}/issues", { params: { path: { version_id: versionId! }, query } })),
    enabled: versionId != null,
    placeholderData: keepPreviousData,
  });
}

export function useRevalidate() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (versionId: number) =>
      unwrap(api.POST("/api/versions/{version_id}/revalidate", { params: { path: { version_id: versionId } } })),
    onSettled: (_data, _error, versionId) => {
      // The version key prefixes its docs and issues.
      void client.invalidateQueries({ queryKey: queryKeys.version(versionId) });
    },
  });
}

export function usePromote() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (versionId: number) =>
      unwrap(api.POST("/api/versions/{version_id}/promote", { params: { path: { version_id: versionId } } })),
    onSettled: () => {
      // DAG summaries, DAG details and node versions all live under "dags"; promotion changes the
      // current flag on both the promoted and the replaced version.
      void client.invalidateQueries({ queryKey: queryKeys.dags() });
      void client.invalidateQueries({ queryKey: ["versions"] });
    },
  });
}

// Compare and search

export function useCompare(a: number | undefined, b: number | undefined) {
  return useQuery({
    queryKey: queryKeys.compare(a ?? -1, b ?? -1),
    queryFn: () => unwrap(api.GET("/api/compare", { params: { query: { a: a!, b: b! } } })),
    enabled: a != null && b != null,
  });
}

export function useCompareDoc(a: number | undefined, b: number | undefined, docId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.compareDoc(a ?? -1, b ?? -1, docId ?? ""),
    queryFn: () =>
      unwrap(api.GET("/api/compare/docs/{doc_id}", { params: { path: { doc_id: docId! }, query: { a: a!, b: b! } } })),
    enabled: a != null && b != null && !!docId,
  });
}

/** Search results for `body`; disabled while it is null. */
export function useSearch(body: SearchIn | null) {
  return useQuery({
    queryKey: queryKeys.search(body!),
    queryFn: () => unwrap(api.POST("/api/search", { body: body! })),
    enabled: body != null && body.query.trim() !== "",
    placeholderData: keepPreviousData,
    staleTime: Infinity,
  });
}

// Sources

export function useSources() {
  return useQuery({ queryKey: queryKeys.sources(), queryFn: () => unwrap(api.GET("/api/sources")) });
}

export function useSnapshotDiff(snapshotId: number | undefined, against?: number) {
  return useQuery({
    queryKey: queryKeys.snapshotDiff(snapshotId ?? -1, against),
    queryFn: () =>
      unwrap(
        api.GET("/api/snapshots/{snapshot_id}/diff", {
          params: { path: { snapshot_id: snapshotId! }, query: against != null ? { against } : {} },
        }),
      ),
    enabled: snapshotId != null,
  });
}

export function useSnapshotDocs(snapshotId: number | undefined, q?: string) {
  return useQuery({
    queryKey: queryKeys.snapshotDocs(snapshotId ?? -1, q),
    queryFn: () =>
      unwrap(
        api.GET("/api/snapshots/{snapshot_id}/docs", {
          params: { path: { snapshot_id: snapshotId! }, query: q ? { q } : {} },
        }),
      ),
    enabled: snapshotId != null,
    placeholderData: keepPreviousData,
  });
}

export function useSync() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (source: string) => unwrap(api.POST("/api/sources/{name}/sync", { params: { path: { name: source } } })),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: queryKeys.sources() });
      void client.invalidateQueries({ queryKey: ["snapshots"] });
    },
  });
}

/** URL of a document's original PDF. */
export function docPdfUrl(docId: string): string {
  return `/api/docs/${encodeURIComponent(docId)}/pdf`;
}
