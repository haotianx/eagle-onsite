import { useState } from "react";

import type { VersionDetail } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

import { PromoteDialog } from "./PromoteDialog";

/** Opens the PromoteDialog; disabled with the reasons in a tooltip when the version isn't promotable. */
export function PromoteButton({ version }: { version: VersionDetail }) {
  const [open, setOpen] = useState(false);
  if (!version.promotable) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span tabIndex={0}>
            <Button size="sm" disabled>
              Promote
            </Button>
          </span>
        </TooltipTrigger>
        <TooltipContent>
          <ul>
            {version.not_promotable_reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </TooltipContent>
      </Tooltip>
    );
  }
  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)}>
        Promote
      </Button>
      <PromoteDialog versionId={version.id} open={open} onOpenChange={setOpen} />
    </>
  );
}
