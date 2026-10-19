import { FileTextIcon, LoaderCircleIcon, XIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import type { UploadCard } from "./api";

export interface DocumentCardProps {
  card: UploadCard;
  /** Opens the document (e.g. in the PDF panel); the card is clickable when set and ready. */
  onOpen?(): void;
  /** Shows ✕ (an unsent card). */
  onRemove?(): void;
  /** A menu trigger shown at the end (a sent card's menu). */
  menu?: ReactNode;
  className?: string;
}

/** The status line under a card's filename. */
export function cardStatus(card: UploadCard): string {
  if (card.removed) return "Removed";
  if (card.status === "failed") return card.error ?? "Failed";
  if (card.status === "processing") {
    if (card.stage === "indexing") return "Indexing…";
    if (card.stage === "reading" && card.pages != null) return `Reading ${card.pages} page${card.pages === 1 ? "" : "s"}…`;
    if (card.stage === "reading") return "Reading…";
    return "Waiting…";
  }
  const meta = [card.job_number, card.project].filter(Boolean).join(" · ");
  if (meta) return meta;
  return card.pages != null ? `${card.pages} page${card.pages === 1 ? "" : "s"}` : "PDF";
}

/** A document attached in a chat: filename and status (processing, ready, failed). */
export function DocumentCard({ card, onOpen, onRemove, menu, className }: DocumentCardProps) {
  const clickable = onOpen != null && card.status === "ready" && !card.removed;
  return (
    <div
      className={cn(
        "flex w-60 items-center gap-2.5 rounded-lg border bg-background p-2 text-left text-sm",
        clickable && "cursor-pointer hover:bg-accent",
        card.status === "failed" && "border-destructive/40",
        className,
      )}
      onClick={clickable ? onOpen : undefined}
      role={clickable ? "button" : undefined}
      tabIndex={clickable ? 0 : undefined}
      onKeyDown={clickable ? (e) => (e.key === "Enter" || e.key === " ") && onOpen?.() : undefined}
    >
      <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
        {card.status === "processing" ? (
          <LoaderCircleIcon className="size-4 animate-spin" aria-hidden />
        ) : (
          <FileTextIcon className="size-4" aria-hidden />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate font-medium">{card.filename}</div>
        <div className={cn("truncate text-xs text-muted-foreground", card.status === "failed" && "text-destructive")}>
          {cardStatus(card)}
        </div>
      </div>
      {onRemove && (
        <Button
          variant="ghost"
          size="icon"
          className="size-6 shrink-0"
          aria-label={`Remove ${card.filename}`}
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
        >
          <XIcon className="size-3.5" />
        </Button>
      )}
      {menu}
    </div>
  );
}
