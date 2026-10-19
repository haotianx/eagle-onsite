import { useMemo } from "react";

import { useDag, useNodeVersions } from "@/api/hooks";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

import { VersionRoleBadge, versionRole, type VersionRole } from "./VersionLabel";

export interface VersionPickerProps {
  dag: string;
  node: string;
  value: number | undefined;
  onChange(versionId: number): void;
  /** Roles to offer; all by default. */
  include?: VersionRole[];
  className?: string;
}

/** Picks one of a node's versions. */
export function VersionPicker({ dag, node, value, onChange, include, className }: VersionPickerProps) {
  const versions = useNodeVersions(dag, node);
  const dagDetail = useDag(dag);
  const options = useMemo(() => {
    const candidates = dagDetail.data?.nodes.find((n) => n.name === node)?.candidates.map((v) => v.id) ?? [];
    return (versions.data ?? [])
      .map((version) => ({ version, role: versionRole(version, candidates) }))
      .filter(({ role }) => !include || include.includes(role));
  }, [versions.data, dagDetail.data, node, include]);

  return (
    <Select value={value == null ? "" : String(value)} onValueChange={(v) => onChange(Number(v))}>
      <SelectTrigger size="sm" className={className} aria-label="Version">
        <SelectValue placeholder={versions.isPending ? "Loading…" : "Select version"} />
      </SelectTrigger>
      <SelectContent>
        {options.map(({ version, role }) => (
          <SelectItem key={version.id} value={String(version.id)}>
            <span className="font-mono">{version.label}</span>
            <VersionRoleBadge role={role} />
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
