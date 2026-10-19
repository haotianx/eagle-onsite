import { useEffect, useRef } from "react";

import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar";
import { Toaster } from "@/components/ui/sonner";

import { useChat } from "./api";
import { ChatPage } from "./ChatPage";
import { PdfPanel } from "./pdf/PdfPanel";
import { PdfPanelProvider, usePdfPanel } from "./pdf-panel";
import { useChatId } from "./route";
import { ChatSidebar } from "./sidebar/ChatSidebar";

/** The chat app: sidebar, conversation and, when a document is open, the PDF panel. */
export function ChatLayout() {
  return (
    <PdfPanelProvider>
      <SidebarProvider className="h-svh">
        <ChatSidebar />
        <SidebarInset className="min-w-0 overflow-hidden">
          <Main />
        </SidebarInset>
      </SidebarProvider>
      <Toaster position="bottom-right" />
    </PdfPanelProvider>
  );
}

function Main() {
  const chatId = useChatId();
  const { target, close } = usePdfPanel();
  const { data: chat } = useChat(chatId);

  useEffect(() => {
    document.title = chat?.title ?? "Chat";
  }, [chat?.title]);

  // Close the PDF panel when switching chats (a new chat getting its id on the first send isn't a switch).
  const shownChatId = useRef(chatId);
  useEffect(() => {
    const previous = shownChatId.current;
    if (previous === chatId) return;
    shownChatId.current = chatId;
    if (previous !== null) close();
  }, [chatId, close]);

  return (
    <ResizablePanelGroup>
      <ResizablePanel id="conversation" minSize={360}>
        <ChatPage chatId={chatId} />
      </ResizablePanel>
      {target && (
        <>
          <ResizableHandle />
          <ResizablePanel id="pdf" defaultSize="45%" minSize={320}>
            <PdfPanel />
          </ResizablePanel>
        </>
      )}
    </ResizablePanelGroup>
  );
}
