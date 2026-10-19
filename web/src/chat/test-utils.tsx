import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import type { ReactNode } from "react";

import { TooltipProvider } from "@/components/ui/tooltip";
import { createTestQueryClient } from "@/test/utils";

import { PdfPanelProvider } from "./pdf-panel";

export { json, mockFetch } from "@/test/utils";

export interface RenderChatResult extends RenderResult {
  queryClient: QueryClient;
}

/** Renders `ui` with the chat app's providers at `path` (default "/chat"). */
export function renderChat(
  ui: ReactNode,
  { path = "/chat", queryClient = createTestQueryClient() }: { path?: string; queryClient?: QueryClient } = {},
): RenderChatResult {
  window.history.replaceState(null, "", path);
  const result = render(
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <PdfPanelProvider>{ui}</PdfPanelProvider>
      </TooltipProvider>
    </QueryClientProvider>,
  );
  return { ...result, queryClient };
}
