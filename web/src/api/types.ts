import type { components } from "./schema";

type Schemas = components["schemas"];

export type StateName = Schemas["NodeInfo"]["state"];
export type RunStatus = Schemas["RunSummary"]["status"];
export type RunKind = Schemas["RunSummary"]["kind"];
export type DocStatus = Schemas["VersionDocOut"]["status"];

export type VersionSummary = Schemas["VersionSummary"];
export type VersionDetail = Schemas["VersionDetail"];
export type InputVersion = Schemas["InputVersion"];
export type IssueCounts = Schemas["IssueCounts"];
export type VersionDocOut = Schemas["VersionDocOut"];
export type VersionDocsPage = Schemas["VersionDocsPage"];
export type VersionDocDetail = Schemas["VersionDocDetail"];
export type IssueOut = Schemas["IssueOut"];
export type DebugFile = Schemas["DebugFile"];
export type RevalidateResult = Schemas["RevalidateResult"];
export type PromoteResult = Schemas["PromoteResult"];
export type NodeInfo = Schemas["NodeInfo"];
export type DagSummary = Schemas["DagSummary"];
export type DagDetail = Schemas["DagDetail"];
export type RunRequestIn = Schemas["RunRequestIn"];
export type PlanInput = Schemas["PlanInput"];
export type NodePlanOut = Schemas["NodePlanOut"];
export type RunPlanOut = Schemas["RunPlanOut"];
export type RunCreated = Schemas["RunCreated"];
export type RunSummary = Schemas["RunSummary"];
export type RunDetail = Schemas["RunDetail"];
export type LogLine = Schemas["LogLine"];
export type NodeMetrics = Schemas["NodeMetrics"];
export type SlowDoc = Schemas["SlowDoc"];
export type RunMetricsOut = Schemas["RunMetricsOut"];
export type DocChangeOut = Schemas["DocChangeOut"];
export type ComparisonOut = Schemas["ComparisonOut"];
export type DocDiffOut = Schemas["DocDiffOut"];
export type SearchIn = Schemas["SearchIn"];
export type SearchHit = Schemas["SearchHit"];
export type SearchSide = Schemas["SearchSide"];
export type SearchOut = Schemas["SearchOut"];
export type SnapshotOut = Schemas["SnapshotOut"];
export type SourceOut = Schemas["SourceOut"];
export type DocRef = Schemas["DocRef"];
export type SnapshotDiffOut = Schemas["SnapshotDiffOut"];
export type SyncOut = Schemas["SyncOut"];
export type SnapshotDocOut = Schemas["SnapshotDocOut"];

/** `data` of each SSE event on /api/runs/{id}/events (event name = kind); not in the OpenAPI schema. */
export interface RunEventOut {
  id: number;
  ts: string;
  kind: string;
  payload: Record<string, unknown>;
}
