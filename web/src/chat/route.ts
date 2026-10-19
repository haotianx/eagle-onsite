import { useSyncExternalStore } from "react";

/** The chat app's two locations: /chat (a new chat) and /chat/<chat id>. The engineer app owns
 * the TanStack router registration, so the chat app follows the URL with the History API. */
const BASE = "/chat";
const listeners = new Set<() => void>();

export function chatPath(chatId: string | null): string {
  return chatId ? `${BASE}/${encodeURIComponent(chatId)}` : BASE;
}

function currentChatId(): string | null {
  const match = /^\/chat\/([^/?#]+)/.exec(window.location.pathname);
  return match ? decodeURIComponent(match[1]) : null;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("popstate", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("popstate", listener);
  };
}

/** Shows `chatId` (null: a new chat), pushing a history entry unless `replace`. */
export function navigateToChat(chatId: string | null, { replace = false }: { replace?: boolean } = {}): void {
  const path = chatPath(chatId);
  if (window.location.pathname !== path) {
    if (replace) window.history.replaceState(null, "", path);
    else window.history.pushState(null, "", path);
  }
  for (const listener of listeners) listener();
}

/** The chat in the URL, or null for a new chat. */
export function useChatId(): string | null {
  return useSyncExternalStore(subscribe, currentChatId, () => null);
}
