import { XIcon } from "lucide-react";
import { useRef, useState } from "react";

import { useSnapshotDocs } from "@/api/hooks";
import type { SnapshotDocOut } from "@/api/types";
import { DocName } from "@/components/DocName";
import { Badge } from "@/components/ui/badge";
import { Command, CommandEmpty, CommandInput, CommandItem, CommandList } from "@/components/ui/command";

import { useDebouncedValue } from "./useDebouncedValue";

export interface DocPickerProps {
  snapshotId: number | undefined;
  value: string[];
  onChange(docIds: string[]): void;
}

/** Searchable multi-select over a snapshot's docs. */
export function DocPicker({ snapshotId, value, onChange }: DocPickerProps) {
  const [q, setQ] = useState("");
  const query = useDebouncedValue(q.trim(), 250);
  const docs = useSnapshotDocs(snapshotId, query || undefined);
  const known = useRef(new Map<string, SnapshotDocOut>());
  for (const doc of docs.data ?? []) known.current.set(doc.doc_id, doc);

  const selected = new Set(value);
  const toggle = (docId: string) =>
    onChange(selected.has(docId) ? value.filter((id) => id !== docId) : [...value, docId]);

  return (
    <div className="space-y-2">
      {value.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {value.map((id) => {
            const name = known.current.get(id)?.filename ?? id.slice(0, 12);
            return (
              <Badge key={id} variant="secondary" className="max-w-full">
                <span className="truncate">{name}</span>
                <button type="button" aria-label={`Remove ${name}`} onClick={() => toggle(id)}>
                  <XIcon />
                </button>
              </Badge>
            );
          })}
        </div>
      )}
      <Command shouldFilter={false} className="rounded-lg border">
        <CommandInput value={q} onValueChange={setQ} placeholder="Find documents" />
        <CommandList className="max-h-48">
          {!docs.isPending && <CommandEmpty>No documents</CommandEmpty>}
          {(docs.data ?? []).map((doc) => (
            <CommandItem
              key={doc.doc_id}
              value={doc.doc_id}
              data-checked={selected.has(doc.doc_id)}
              onSelect={() => toggle(doc.doc_id)}
            >
              <DocName doc={doc} />
            </CommandItem>
          ))}
        </CommandList>
      </Command>
    </div>
  );
}
