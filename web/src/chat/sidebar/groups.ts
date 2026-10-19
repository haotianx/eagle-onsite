import type { ChatSummary } from "../api";

export type ChatGroupLabel = "Today" | "Yesterday" | "Previous 7 days" | "Older";

export interface ChatGroup {
  label: ChatGroupLabel;
  chats: ChatSummary[];
}

const DAY_MS = 24 * 60 * 60 * 1000;

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

function label(updatedAt: string, today: number): ChatGroupLabel {
  // Rounded: a day across a DST change is 23 or 25 hours.
  const days = Math.round((today - startOfDay(new Date(updatedAt))) / DAY_MS);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days <= 7) return "Previous 7 days";
  return "Older";
}

/** Chats newest first, grouped by the local calendar day of their last update. Empty groups are left out. */
export function groupChats(chats: ChatSummary[], now: Date = new Date()): ChatGroup[] {
  const today = startOfDay(now);
  const sorted = [...chats].sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at));
  const groups: ChatGroup[] = [];
  for (const chat of sorted) {
    const name = label(chat.updated_at, today);
    const last = groups.at(-1);
    if (last?.label === name) last.chats.push(chat);
    else groups.push({ label: name, chats: [chat] });
  }
  return groups;
}
