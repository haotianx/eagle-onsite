import { useQueryClient } from "@tanstack/react-query";
import { MoreHorizontalIcon, SquarePenIcon } from "lucide-react";
import { type MouseEvent, useEffect, useMemo } from "react";

import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSkeleton,
  SidebarTrigger,
} from "@/components/ui/sidebar";

import { chatKeys, type ChatSummary, useChats } from "../api";
import { chatPath, navigateToChat, useChatId } from "../route";
import { ChatMenu } from "./ChatMenu";
import { groupChats } from "./groups";

/** How often the list refetches while an answer is generating, so ● clears for chats not on screen. */
export const GENERATING_REFETCH_MS = 3_000;

/** Follows a plain click in-app; modified clicks keep the link's default (e.g. a new tab). */
function follow(chatId: string | null) {
  return (event: MouseEvent) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigateToChat(chatId);
  };
}

export function ChatSidebar() {
  const chats = useChats();
  const openChatId = useChatId();
  const groups = useMemo(() => groupChats(chats.data?.chats ?? []), [chats.data]);
  useRefetchWhileGenerating(chats.data?.chats);

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarTrigger />
          </SidebarMenuItem>
          <SidebarMenuItem>
            <SidebarMenuButton asChild tooltip="New chat">
              <a href={chatPath(null)} onClick={follow(null)}>
                <SquarePenIcon />
                <span>New chat</span>
              </a>
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent className="group-data-[collapsible=icon]:hidden">
        {chats.isPending ? (
          <SidebarGroup>
            <SidebarMenu>
              {Array.from({ length: 4 }, (_, i) => (
                <SidebarMenuItem key={i}>
                  <SidebarMenuSkeleton />
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroup>
        ) : (
          groups.map((group) => (
            <SidebarGroup key={group.label}>
              <SidebarGroupLabel>{group.label}</SidebarGroupLabel>
              <SidebarMenu>
                {group.chats.map((chat) => (
                  <ChatRow key={chat.id} chat={chat} active={chat.id === openChatId} />
                ))}
              </SidebarMenu>
            </SidebarGroup>
          ))
        )}
      </SidebarContent>
    </Sidebar>
  );
}

function ChatRow({ chat, active }: { chat: ChatSummary; active: boolean }) {
  const title = chat.title ?? "New chat";
  return (
    <SidebarMenuItem>
      <SidebarMenuButton asChild isActive={active} className="pr-8">
        <a href={chatPath(chat.id)} onClick={follow(chat.id)} aria-current={active ? "page" : undefined}>
          <span className="truncate">{title}</span>
          {chat.generating && (
            <span role="img" aria-label="Generating" className="size-1.5 shrink-0 rounded-full bg-foreground/60" />
          )}
        </a>
      </SidebarMenuButton>
      <ChatMenu
        chat={chat}
        trigger={
          <SidebarMenuAction showOnHover aria-label={`Options for ${title}`}>
            <MoreHorizontalIcon />
          </SidebarMenuAction>
        }
      />
    </SidebarMenuItem>
  );
}

function useRefetchWhileGenerating(chats: ChatSummary[] | undefined) {
  const client = useQueryClient();
  const generating = chats?.some((chat) => chat.generating) ?? false;
  useEffect(() => {
    if (!generating) return;
    const timer = setInterval(
      () => void client.invalidateQueries({ queryKey: chatKeys.chats(), exact: true }),
      GENERATING_REFETCH_MS,
    );
    return () => clearInterval(timer);
  }, [generating, client]);
}
