import type { ChatSummary } from "../api";
import { groupChats } from "./groups";

const chat = (id: string, updatedAt: Date): ChatSummary => ({
  id,
  title: id,
  generating: false,
  updated_at: updatedAt.toISOString(),
});

test("chats group by local day, newest first", () => {
  const now = new Date(2026, 9, 19, 9, 30);
  const groups = groupChats(
    [
      chat("older", new Date(2026, 9, 11, 23, 0)),
      chat("early-today", new Date(2026, 9, 19, 0, 5)),
      chat("late-yesterday", new Date(2026, 9, 18, 23, 59)),
      chat("week", new Date(2026, 9, 12, 8, 0)),
      chat("today", new Date(2026, 9, 19, 9, 0)),
      chat("two-days", new Date(2026, 9, 17, 12, 0)),
      chat("last-year", new Date(2025, 0, 1)),
    ],
    now,
  );
  expect(groups.map((g) => [g.label, g.chats.map((c) => c.id)])).toEqual([
    ["Today", ["today", "early-today"]],
    ["Yesterday", ["late-yesterday"]],
    ["Previous 7 days", ["two-days", "week"]],
    ["Older", ["older", "last-year"]],
  ]);
});

test("empty groups are left out", () => {
  const now = new Date(2026, 9, 19, 12, 0);
  expect(groupChats([chat("a", new Date(2026, 8, 1))], now).map((g) => g.label)).toEqual(["Older"]);
  expect(groupChats([], now)).toEqual([]);
});
