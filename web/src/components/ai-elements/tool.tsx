"use client";

// Adapted from AI Elements' Tool: a borderless collapsible line (the Claude desktop app's
// search step) instead of a card with AI SDK tool states, input and output.

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import { ChevronRightIcon } from "lucide-react";
import type { ComponentProps } from "react";

export type ToolProps = ComponentProps<typeof Collapsible>;

export const Tool = ({ className, ...props }: ToolProps) => (
  <Collapsible className={cn("not-prose w-full", className)} {...props} />
);

export type ToolHeaderProps = ComponentProps<typeof CollapsibleTrigger> & {
  title: string;
  /** Shimmers the title while the tool runs. */
  running?: boolean;
};

export const ToolHeader = ({
  className,
  title,
  running = false,
  ...props
}: ToolHeaderProps) => (
  <CollapsibleTrigger
    className={cn(
      "group flex items-center gap-1 text-muted-foreground text-sm transition-colors hover:text-foreground",
      className
    )}
    {...props}
  >
    <ChevronRightIcon className="size-4 shrink-0 transition-transform group-data-[state=open]:rotate-90" />
    <span className={cn(running && "animate-pulse")}>{title}</span>
  </CollapsibleTrigger>
);

export type ToolContentProps = ComponentProps<typeof CollapsibleContent>;

export const ToolContent = ({ className, ...props }: ToolContentProps) => (
  <CollapsibleContent
    className={cn(
      "data-[state=closed]:fade-out-0 data-[state=closed]:slide-out-to-top-2 data-[state=open]:slide-in-from-top-2 text-popover-foreground outline-none data-[state=closed]:animate-out data-[state=open]:animate-in",
      className
    )}
    {...props}
  />
);
