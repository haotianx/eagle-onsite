import { VersionRoleBadge } from "@/components/VersionLabel";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

import type { EmbedOption } from "./embedVersions";

export interface EmbedVersionPickerProps {
  options: EmbedOption[];
  value: number | undefined;
  onChange(versionId: number): void;
  isPending?: boolean;
  label: string;
}

/** Picks a version of any embed node. */
export function EmbedVersionPicker({ options, value, onChange, isPending, label }: EmbedVersionPickerProps) {
  return (
    <Select value={value == null ? "" : String(value)} onValueChange={(v) => onChange(Number(v))}>
      <SelectTrigger size="sm" aria-label={label}>
        <SelectValue placeholder={isPending ? "Loading…" : "Select version"} />
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
