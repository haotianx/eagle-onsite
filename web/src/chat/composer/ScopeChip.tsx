import { ChevronDownIcon } from "lucide-react";

import { PromptInputButton } from "@/components/ai-elements/prompt-input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

import type { ChatDetail } from "../api";
import type { UnsentCard } from "./useUpload";

export interface ScopeOption {
  docId: string;
  filename: string;
}

const ALL = "";

/** The documents attached in this chat: ready, non-removed attachments of its sent questions, then
 * ready unsent cards, once per document. */
export function scopeOptions(chat: ChatDetail | null, cards: UnsentCard[]): ScopeOption[] {
  const attached = [
    ...(chat?.messages ?? []).flatMap((m) => (m.role === "user" ? m.attachments : [])),
    ...cards.filter((u) => !u.pending).map((u) => u.card),
  ];
  const options = new Map<string, ScopeOption>();
  for (const card of attached) {
    if (card.status === "ready" && !card.removed && !options.has(card.doc_id)) {
      options.set(card.doc_id, { docId: card.doc_id, filename: card.filename });
    }
  }
  return [...options.values()];
}

export interface ScopeChipProps {
  options: ScopeOption[];
  /** Doc id, or null for All documents. */
  value: string | null;
  onChange(docId: string | null): void;
  disabled?: boolean;
}

/** What a question searches: All documents or one document attached in this chat. */
export function ScopeChip({ options, value, onChange, disabled }: ScopeChipProps) {
  const label = options.find((o) => o.docId === value)?.filename ?? "All documents";
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild disabled={disabled}>
        <PromptInputButton className="min-w-0 max-w-64 text-muted-foreground" size="sm">
          <span className="truncate">{label}</span>
          <ChevronDownIcon className="size-3.5 shrink-0" />
        </PromptInputButton>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-auto max-w-80 min-w-48">
        <DropdownMenuRadioGroup value={value ?? ALL} onValueChange={(v) => onChange(v === ALL ? null : v)}>
          <DropdownMenuRadioItem value={ALL}>All documents</DropdownMenuRadioItem>
          {options.length > 0 && <DropdownMenuSeparator />}
          {options.map((o) => (
            <DropdownMenuRadioItem key={o.docId} value={o.docId}>
              <span className="truncate">{o.filename}</span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
