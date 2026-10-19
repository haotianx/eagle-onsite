import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { ChatDetail, ChatMessage } from "./api";
import { ChatPage } from "./ChatPage";
import { json, mockFetch, renderChat } from "./test-utils";

vi.mock("./messages/MessageList", () => ({
  MessageList: ({ chat }: { chat: ChatDetail }) => <div data-testid="messages">{chat.id}</div>,
}));
vi.mock("./composer/Composer", () => ({
  Composer: ({ chat }: { chat: ChatDetail | null }) => <div data-testid="composer">{chat?.id ?? "new"}</div>,
}));

const question = { id: "m1", role: "user", text: "What fees?" } as unknown as ChatMessage;

const detail = (overrides: Partial<ChatDetail> = {}): ChatDetail => ({
  id: "c1",
  title: "Szekeres fees",
  generating: false,
  scope: null,
  messages: [question],
  ...overrides,
});

test("a new chat shows only the composer", () => {
  const fetch = mockFetch({});
  renderChat(<ChatPage chatId={null} />);
  expect(screen.getByTestId("composer")).toHaveTextContent("new");
  expect(screen.queryByTestId("messages")).not.toBeInTheDocument();
  expect(screen.queryByRole("heading")).not.toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});

test("a chat without messages shows only the composer", async () => {
  mockFetch({ "GET /api/chat/chats/:id": detail({ title: null, messages: [] }) });
  renderChat(<ChatPage chatId="c1" />, { path: "/chat/c1" });
  expect(await screen.findByTestId("composer")).toHaveTextContent("c1");
  expect(screen.queryByTestId("messages")).not.toBeInTheDocument();
  expect(screen.queryByRole("heading")).not.toBeInTheDocument();
});

test("a chat shows its title, messages and composer", async () => {
  mockFetch({ "GET /api/chat/chats/:id": detail({ title: null }) });
  renderChat(<ChatPage chatId="c1" />, { path: "/chat/c1" });
  expect(await screen.findByRole("heading", { name: "New chat" })).toBeInTheDocument();
  expect(screen.getByTestId("messages")).toHaveTextContent("c1");
  expect(screen.getByTestId("composer")).toHaveTextContent("c1");
});

test("a missing chat goes to a new chat", async () => {
  mockFetch({ "GET /api/chat/chats/:id": json({ detail: "Chat not found" }, 404) });
  const before = window.history.length;
  renderChat(<ChatPage chatId="gone" />, { path: "/chat/gone" });
  await vi.waitFor(() => expect(window.location.pathname).toBe("/chat"));
  expect(window.history.length).toBe(before);
});

test("the title menu renames and deletes the chat", async () => {
  let chat = detail();
  mockFetch({
    "GET /api/chat/chats/:id": () => chat,
    "PATCH /api/chat/chats/:id": async (request: Request) => {
      chat = { ...chat, title: ((await request.json()) as { title: string }).title };
      return { id: chat.id, title: chat.title, generating: false, updated_at: new Date().toISOString() };
    },
    "DELETE /api/chat/chats/:id": () => new Response(null, { status: 204 }),
    "GET /api/chat/chats": { chats: [], library_ready: true },
  });
  renderChat(<ChatPage chatId="c1" />, { path: "/chat/c1" });

  await userEvent.click(await screen.findByRole("button", { name: "Chat options" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Rename" }));
  const input = screen.getByRole("textbox", { name: "Chat title" });
  await userEvent.clear(input);
  await userEvent.type(input, "Fees{Enter}");
  expect(await screen.findByRole("heading", { name: "Fees" })).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Chat options" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Delete" }));
  await userEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Delete" }));
  await vi.waitFor(() => expect(window.location.pathname).toBe("/chat"));
});
