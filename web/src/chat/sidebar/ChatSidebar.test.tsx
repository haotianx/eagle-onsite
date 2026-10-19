import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SidebarProvider } from "@/components/ui/sidebar";

import type { ChatList, ChatSummary } from "../api";
import { json, mockFetch, renderChat } from "../test-utils";
import { ChatSidebar, GENERATING_REFETCH_MS } from "./ChatSidebar";

const NOW = new Date(2026, 9, 19, 12, 0);

const summary = (id: string, title: string | null, updatedAt: Date, generating = false): ChatSummary => ({
  id,
  title,
  generating,
  updated_at: updatedAt.toISOString(),
});

const CHATS: ChatSummary[] = [
  summary("c1", "Szekeres fees", new Date(2026, 9, 19, 11, 0), true),
  summary("c2", null, new Date(2026, 9, 19, 10, 0)),
  summary("c3", "Retainer terms", new Date(2026, 9, 18, 9, 0)),
  summary("c4", "Grove Plaza", new Date(2026, 9, 14, 9, 0)),
  summary("c5", "Elliot scope", new Date(2026, 7, 1, 9, 0)),
];

const list = (chats: ChatSummary[] = CHATS): ChatList => ({ chats, library_ready: true });

function renderSidebar(path = "/chat/c3") {
  return renderChat(
    <SidebarProvider>
      <ChatSidebar />
    </SidebarProvider>,
    { path },
  );
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

test("chats are grouped by day, newest first, with the open chat highlighted", async () => {
  mockFetch({ "GET /api/chat/chats": list() });
  renderSidebar();

  await screen.findByText("Szekeres fees");
  const groups = screen.getAllByText(/^(Today|Yesterday|Previous 7 days|Older)$/).map((el) => el.textContent);
  expect(groups).toEqual(["Today", "Yesterday", "Previous 7 days", "Older"]);

  const links = screen.getAllByRole("link").map((el) => el.textContent);
  expect(links).toEqual(["New chat", "Szekeres fees", "New chat", "Retainer terms", "Grove Plaza", "Elliot scope"]);

  expect(screen.getByRole("link", { name: "Retainer terms" })).toHaveAttribute("aria-current", "page");
  const generating = screen.getByRole("link", { name: /^Szekeres fees/ });
  expect(generating).not.toHaveAttribute("aria-current");
  expect(within(generating).getByRole("img", { name: "Generating" })).toBeInTheDocument();
  expect(screen.getAllByRole("img", { name: "Generating" })).toHaveLength(1);
});

test("New chat and rows navigate", async () => {
  mockFetch({ "GET /api/chat/chats": list() });
  renderSidebar();

  await userEvent.click(await screen.findByRole("link", { name: "Grove Plaza" }));
  expect(window.location.pathname).toBe("/chat/c4");
  expect(screen.getByRole("link", { name: "Grove Plaza" })).toHaveAttribute("aria-current", "page");

  await userEvent.click(screen.getAllByRole("link", { name: "New chat" })[0]);
  expect(window.location.pathname).toBe("/chat");
  expect(screen.queryByRole("link", { current: "page" })).not.toBeInTheDocument();
});

test("rename saves on Enter and cancels on Escape", async () => {
  let chats = CHATS;
  const fetch = mockFetch({
    "GET /api/chat/chats": () => list(chats),
    "PATCH /api/chat/chats/:id": async (request: Request, { id }: Record<string, string>) => {
      const { title } = (await request.json()) as { title: string };
      chats = chats.map((c) => (c.id === id ? { ...c, title } : c));
      return chats.find((c) => c.id === id);
    },
  });
  renderSidebar();

  await userEvent.click(await screen.findByRole("button", { name: "Options for Retainer terms" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
  const input = screen.getByRole("textbox", { name: "Chat title" });
  expect(input).toHaveValue("Retainer terms");
  await userEvent.clear(input);
  await userEvent.type(input, "Ignored{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(fetch.mock.calls.some(([r]) => (r as Request).method === "PATCH")).toBe(false);

  await userEvent.click(screen.getByRole("button", { name: "Options for Retainer terms" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
  const again = screen.getByRole("textbox", { name: "Chat title" });
  await userEvent.clear(again);
  await userEvent.type(again, "Retainer schedule{Enter}");

  expect(await screen.findByRole("link", { name: "Retainer schedule" })).toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  const patch = fetch.mock.calls.map(([r]) => r as Request).find((r) => r.method === "PATCH")!;
  expect(new URL(patch.url).pathname).toBe("/api/chat/chats/c3");
});

test("deleting the open chat confirms by name and goes to a new chat", async () => {
  let chats = CHATS;
  const fetch = mockFetch({
    "GET /api/chat/chats": () => list(chats),
    "DELETE /api/chat/chats/:id": (_: Request, { id }: Record<string, string>) => {
      chats = chats.filter((c) => c.id !== id);
      return new Response(null, { status: 204 });
    },
  });
  renderSidebar("/chat/c3");

  await userEvent.click(await screen.findByRole("button", { name: "Options for Retainer terms" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
  const dialog = screen.getByRole("alertdialog");
  expect(dialog).toHaveTextContent("Delete “Retainer terms”?");
  await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));

  await vi.waitFor(() => expect(window.location.pathname).toBe("/chat"));
  expect(screen.queryByRole("link", { name: "Retainer terms" })).not.toBeInTheDocument();
  expect(fetch.mock.calls.filter(([r]) => (r as Request).method === "DELETE")).toHaveLength(1);
});

test("deleting another chat stays on the open chat", async () => {
  mockFetch({
    "GET /api/chat/chats": list(),
    "DELETE /api/chat/chats/:id": () => new Response(null, { status: 204 }),
  });
  renderSidebar("/chat/c3");

  await userEvent.click(await screen.findByRole("button", { name: "Options for Grove Plaza" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }));
  await vi.waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  expect(window.location.pathname).toBe("/chat/c3");
});

test("the list refetches while a chat is generating", async () => {
  vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
  vi.setSystemTime(NOW);
  let chats = CHATS;
  const fetch = mockFetch({ "GET /api/chat/chats": () => list(chats) });
  renderSidebar();
  await screen.findByRole("img", { name: "Generating" });
  expect(fetch).toHaveBeenCalledTimes(1);

  chats = CHATS.map((c) => ({ ...c, generating: false }));
  vi.advanceTimersByTime(GENERATING_REFETCH_MS);
  await vi.waitFor(() => expect(screen.queryByRole("img", { name: "Generating" })).not.toBeInTheDocument());
  expect(fetch).toHaveBeenCalledTimes(2);

  vi.advanceTimersByTime(GENERATING_REFETCH_MS * 3);
  expect(fetch).toHaveBeenCalledTimes(2);
});

test("a failed list shows no rows", async () => {
  mockFetch({ "GET /api/chat/chats": json({ detail: "boom" }, 500) });
  renderSidebar();
  await vi.waitFor(() => expect(screen.queryByText("Today")).not.toBeInTheDocument());
  expect(screen.getAllByRole("link")).toHaveLength(1);
});
