import { useQueries } from "@tanstack/react-query";

import { api, unwrap } from "@/api/client";
import { queryKeys, useDags } from "@/api/hooks";
import type { NodeInfo, VersionSummary } from "@/api/types";
import { versionRole, type VersionRole } from "@/components/VersionLabel";

/** An embed node and its versions (newest first). */
export interface EmbedNode {
  dag: string;
  node: NodeInfo;
  versions: VersionSummary[];
}

export interface EmbedOption {
  version: VersionSummary;
  role: VersionRole;
}

export function embedOptions(nodes: EmbedNode[]): EmbedOption[] {
  return nodes.flatMap(({ node, versions }) => {
    const candidates = node.candidates.map((v) => v.id);
    return versions.map((version) => ({ version, role: versionRole(version, candidates) }));
  });
}

function newest(versions: VersionSummary[]): VersionSummary | undefined {
  return versions.reduce<VersionSummary | undefined>(
    (best, v) =>
      !best || v.created_at > best.created_at || (v.created_at === best.created_at && v.id > best.id) ? v : best,
    undefined,
  );
}

/** Left: the first embed node's current version (else its newest). Right: the newest candidate,
 * preferring the left's node; else the newest other version, likewise. */
export function defaultVersions(nodes: EmbedNode[]): { a?: number; b?: number } {
  const home = nodes.find((n) => n.node.current) ?? nodes.find((n) => n.versions.length > 0);
  if (!home) return {};
  const a = home.node.current?.id ?? newest(home.versions)!.id;
  const rest = nodes.flatMap((n) => n.versions.filter((v) => v.id !== a).map((v) => ({ n, v })));
  const candidates = rest.filter(({ n, v }) => n.node.candidates.some((c) => c.id === v.id));
  const pick = (items: typeof rest) => newest(items.map(({ v }) => v));
  const atHome = (items: typeof rest) => items.filter(({ n }) => n === home);
  const b = pick(atHome(candidates)) ?? pick(candidates) ?? pick(atHome(rest)) ?? pick(rest);
  return { a, b: b?.id };
}

/** Every embed node across DAGs with its versions. */
export function useEmbedNodes(): { nodes: EmbedNode[]; isPending: boolean; error: Error | null } {
  const dags = useDags();
  const names = (dags.data ?? []).map((d) => d.name);
  const details = useQueries({
    queries: names.map((dag) => ({
      queryKey: queryKeys.dag(dag),
      queryFn: () => unwrap(api.GET("/api/dags/{dag}", { params: { path: { dag } } })),
    })),
  });
  const embed = details.flatMap((d) =>
    (d.data?.nodes ?? []).filter((n) => n.artifact_type === "embed").map((node) => ({ dag: d.data!.name, node })),
  );
  const versions = useQueries({
    queries: embed.map(({ dag, node }) => ({
      queryKey: queryKeys.nodeVersions(dag, node.name),
      queryFn: () =>
        unwrap(api.GET("/api/dags/{dag}/nodes/{node}/versions", { params: { path: { dag, node: node.name } } })),
    })),
  });

  const isPending = dags.isPending || details.some((d) => d.isPending) || versions.some((v) => v.isPending);
  const error = dags.error ?? details.find((d) => d.error)?.error ?? versions.find((v) => v.error)?.error ?? null;
  const nodes = embed.map((e, i) => ({ ...e, versions: versions[i]?.data ?? [] }));
  return { nodes, isPending, error };
}
