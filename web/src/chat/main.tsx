import "./chat.css";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { TooltipProvider } from "@/components/ui/tooltip";

import { ChatLayout } from "./ChatLayout";

const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 5_000 } } });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <ChatLayout />
      </TooltipProvider>
    </QueryClientProvider>
  </StrictMode>,
);
