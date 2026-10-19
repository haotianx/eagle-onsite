import { MoreHorizontalIcon } from "lucide-react";
import { useEffect } from "react";

import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

import { useChat } from "./api";
import { Composer } from "./composer/Composer";
import { MessageList } from "./messages/MessageList";
import { navigateToChat } from "./route";
import { ChatMenu } from "./sidebar/ChatMenu";

/** The conversation: `chatId` null is a new chat. */
export function ChatPage({ chatId }: { chatId: string | null }) {
  const { data: chat, error, isPending } = useChat(chatId);
  const notFound = error instanceof ApiError && error.status === 404;

  useEffect(() => {
    if (notFound) navigateToChat(null, { replace: true });
  }, [notFound]);

  if (chatId != null && chat == null) {
    if (isPending) return <LoadingSkeleton />;
    if (notFound) return null;
    return (
      <div className="flex h-full items-center justify-center p-4 text-sm text-muted-foreground">
        {error?.message}
      </div>
    );
  }

  const empty = chat == null || chat.messages.length === 0;
  // The composer keeps its place in the tree, so it stays mounted when a chat gets its first message.
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex h-12 shrink-0 items-center gap-1 px-4">
        {chat && !empty && (
          <>
            <h1 className="truncate text-sm font-medium">{chat.title ?? "New chat"}</h1>
            <ChatMenu
              chat={chat}
              trigger={
                <Button variant="ghost" size="icon-sm" aria-label="Chat options">
                  <MoreHorizontalIcon />
                </Button>
              }
            />
          </>
        )}
      </header>
      {empty ? (
        <div className="flex-1" />
      ) : (
        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
          <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col px-4">
            <MessageList chat={chat} />
          </div>
        </div>
      )}
      <div className="mx-auto w-full max-w-3xl shrink-0 px-4 pb-4">
        <Composer chat={chat ?? null} />
      </div>
      {empty && <div className="flex-1 pb-12" />}
    </div>
  );
}

function LoadingSkeleton() {
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-12 shrink-0 items-center px-4">
        <Skeleton className="h-4 w-40" />
      </div>
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 pt-6">
        <Skeleton className="ml-auto h-10 w-1/2" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-5/6" />
      </div>
    </div>
  );
}
