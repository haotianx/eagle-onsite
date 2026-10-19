import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { FakeEventSource } from "@/test/fake-event-source";

import {
  type AskResponse,
  type ChatDetail,
  chatKeys,
  type UploadCard,
  uploadEventsUrl,
  type UserMessage,
  useChat,
} from "../api";
import { usePdfPanel } from "../pdf-panel";
import { navigateToChat, useChatId } from "../route";
import { json, mockFetch, renderChat } from "../test-utils";
import { Composer } from "./Composer";

vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));

const navigation = vi.hoisted(() => ({ onNavigate: (_chatId: string | null) => {} }));
vi.mock("../route", async (importOriginal) => {
  const route = await importOriginal<typeof import("../route")>();
  return {
    ...route,
    navigateToChat: (...args: Parameters<typeof route.navigateToChat>) => {
      navigation.onNavigate(args[0]);
      route.navigateToChat(...args);
    },
  };
});

function card(extra: Partial<UploadCard> = {}): UploadCard {
  return {
    id: "u1",
    doc_id: "d1",
    filename: "PROPOSAL ADDENDUM.pdf",
    pages: null,
    status: "processing",
    stage: "queued",
    error: null,
    removable: true,
    removed: false,
    job_number: null,
    project: null,
    ...extra,
  };
}

function question(attachments: UploadCard[], extra: Partial<UserMessage> = {}): UserMessage {
  return { id: "m1", role: "user", text: "Q", scope: null, attachments, created_at: "2026-10-19T12:00:00Z", ...extra };
}

function chat(extra: Partial<ChatDetail> = {}): ChatDetail {
  return { id: "c1", title: null, generating: false, scope: null, messages: [], ...extra };
}

const askResponse: AskResponse = {
  question: question([], { id: "q2" }),
  answer: {
    id: "a2",
    role: "assistant",
    parts: [],
    citations: [],
    search_steps: [],
    scope: null,
    state: "generating",
    error: null,
    created_at: "2026-10-19T12:00:00Z",
  },
};

function pdf(name = "PROPOSAL ADDENDUM.pdf"): File {
  return new File(["%PDF-1.7"], name, { type: "application/pdf" });
}

type Routes = Parameters<typeof mockFetch>[0];

/** Mocks the chat API; `asks` collects the bodies posted to /messages. */
function mockApi(routes: Routes = {}, { libraryReady = true, detail = chat() } = {}) {
  const asks: unknown[] = [];
  const fetchMock = mockFetch({
    "GET /api/chat/chats": { chats: [], library_ready: libraryReady },
    "GET /api/chat/chats/:id": (_: Request, { id }: Record<string, string>) => ({ ...detail, id }),
    "POST /api/chat/chats/:id/messages": async (request: Request) => {
      asks.push(await request.json());
      return askResponse;
    },
    ...routes,
  });
  return { fetchMock, asks };
}

function calls(fetchMock: ReturnType<typeof mockFetch>, method: string, path: string): Request[] {
  return fetchMock.mock.calls
    .map(([input]) => input as Request)
    .filter((r) => r.method === method && new URL(r.url).pathname === path);
}

/** The composer of the chat in the URL, as the chat page renders it. */
function CurrentChat() {
  const chatId = useChatId();
  const { data } = useChat(chatId);
  return chatId != null && data == null ? null : <Composer chat={data ?? null} />;
}

/** The composer of chat c1, following its cached detail. */
function OpenChat() {
  const { data } = useChat("c1");
  return data ? <Composer chat={data} /> : null;
}

function PanelTarget() {
  const { target } = usePdfPanel();
  return <output>{target && `${target.docId} ${target.filename}`}</output>;
}

async function renderOpenChat() {
  const result = renderChat(<OpenChat />, { path: "/chat/c1" });
  await screen.findByPlaceholderText("Ask a question");
  return result;
}

const textbox = () => screen.getByRole("textbox");
const sendButton = () => screen.getByRole("button", { name: "Send" });
const scopeChip = (name: string) => screen.getByRole("button", { name });

beforeEach(() => {
  vi.mocked(toast.error).mockClear();
});

describe("Composer", () => {
  it("sends on Enter and adds a line on Shift+Enter", async () => {
    const { asks } = mockApi();
    await renderOpenChat();
    expect(sendButton()).toBeDisabled();

    await userEvent.type(textbox(), "What fees?{Shift>}{Enter}{/Shift}And terms?");
    expect(textbox()).toHaveValue("What fees?\nAnd terms?");
    expect(asks).toHaveLength(0);

    await userEvent.type(textbox(), "{Enter}");
    await waitFor(() => expect(textbox()).toHaveValue(""));
    expect(asks).toEqual([{ text: "What fees?\nAnd terms?", scope: null, upload_ids: [] }]);
  });

  it("creates a chat on the first send and opens it", async () => {
    const { fetchMock, asks } = mockApi({
      "POST /api/chat/chats": { id: "new1", title: null, generating: false, updated_at: "2026-10-19T12:00:00Z" },
    });
    const { queryClient } = renderChat(<CurrentChat />);
    let cached: ChatDetail | undefined;
    navigation.onNavigate = (chatId) => {
      cached = queryClient.getQueryData<ChatDetail>(chatKeys.chat(chatId!));
    };
    const field = textbox();

    await userEvent.type(field, "Which proposals are for Grove Plaza?{Enter}");
    await waitFor(() => expect(window.location.pathname).toBe("/chat/new1"));
    expect(calls(fetchMock, "POST", "/api/chat/chats")).toHaveLength(1);
    expect(asks).toEqual([{ text: "Which proposals are for Grove Plaza?", scope: null, upload_ids: [] }]);
    // The new chat's detail is cached with the question and the generating answer.
    expect(cached).toMatchObject({ id: "new1", generating: true, messages: [{ id: "q2" }, { id: "a2" }] });
    // The composer stays mounted as the new chat opens.
    expect(textbox()).toBe(field);
    expect(field).toHaveValue("");
  });

  it("starts a fresh draft when another chat opens", async () => {
    mockApi();
    renderChat(<CurrentChat />, { path: "/chat/c1" });
    await screen.findByPlaceholderText("Ask a question");
    await userEvent.type(textbox(), "Draft");

    act(() => navigateToChat(null));
    expect(textbox()).toHaveValue("");
  });

  it("follows an upload to ready, switches the scope and attaches it", async () => {
    const { asks } = mockApi({ "POST /api/chat/uploads": card() });
    await renderOpenChat();

    await userEvent.upload(screen.getByLabelText("Upload files"), pdf());
    expect(await screen.findByText("Waiting…")).toBeInTheDocument();
    await userEvent.type(textbox(), "Summarize this");
    expect(sendButton()).toBeDisabled();

    const source = FakeEventSource.last(uploadEventsUrl("u1"))!;
    act(() => source.emit("status", { id: "u1", doc_id: "d1", status: "processing", stage: "reading", pages: 3, error: null }));
    expect(screen.getByText("Reading 3 pages…")).toBeInTheDocument();
    expect(scopeChip("All documents")).toBeInTheDocument();

    act(() => source.emit("status", { id: "u1", doc_id: "d1", status: "ready", stage: null, pages: 3, error: null }));
    expect(screen.getByText("3 pages")).toBeInTheDocument();
    expect(source.closed).toBe(true);
    expect(scopeChip("PROPOSAL ADDENDUM.pdf")).toBeInTheDocument();
    expect(sendButton()).toBeEnabled();

    await userEvent.click(sendButton());
    await waitFor(() => expect(screen.queryByText("3 pages")).not.toBeInTheDocument());
    expect(asks).toEqual([{ text: "Summarize this", scope: "d1", upload_ids: ["u1"] }]);
  });

  it("shows a failed upload's reason and drops it on ✕", async () => {
    const { fetchMock } = mockApi({
      "POST /api/chat/uploads": card({ status: "failed", stage: null, error: "Encrypted", removable: false }),
    });
    await renderOpenChat();

    await userEvent.upload(screen.getByLabelText("Upload files"), pdf());
    expect(await screen.findByText("Encrypted")).toBeInTheDocument();
    expect(FakeEventSource.instances).toHaveLength(0);
    await userEvent.type(textbox(), "Hi");
    expect(sendButton()).toBeEnabled();

    await userEvent.click(screen.getByRole("button", { name: "Remove PROPOSAL ADDENDUM.pdf" }));
    expect(screen.queryByText("Encrypted")).not.toBeInTheDocument();
    expect(calls(fetchMock, "DELETE", "/api/chat/uploads/u1")).toHaveLength(0);
  });

  it("removes a removable upload from the library on ✕ and the scope returns to All documents", async () => {
    const { fetchMock } = mockApi({
      "POST /api/chat/uploads": card({ status: "ready", stage: null, pages: 2 }),
      "DELETE /api/chat/uploads/:id": card({ status: "ready", stage: null, removed: true }),
    });
    await renderOpenChat();

    await userEvent.upload(screen.getByLabelText("Upload files"), pdf());
    expect(await screen.findByText("2 pages")).toBeInTheDocument();
    expect(scopeChip("PROPOSAL ADDENDUM.pdf")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Remove PROPOSAL ADDENDUM.pdf" }));
    await waitFor(() => expect(screen.queryByText("2 pages")).not.toBeInTheDocument());
    expect(calls(fetchMock, "DELETE", "/api/chat/uploads/u1")).toHaveLength(1);
    expect(scopeChip("All documents")).toBeInTheDocument();
  });

  it("drops a non-removable card without leaving the library", async () => {
    const { fetchMock } = mockApi({
      "POST /api/chat/uploads": card({ status: "ready", stage: null, removable: false, job_number: "2292" }),
    });
    await renderOpenChat();

    await userEvent.upload(screen.getByLabelText("Upload files"), pdf());
    expect(await screen.findByText("2292")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Remove PROPOSAL ADDENDUM.pdf" }));
    expect(screen.queryByText("2292")).not.toBeInTheDocument();
    expect(calls(fetchMock, "DELETE", "/api/chat/uploads/u1")).toHaveLength(0);
  });

  it("offers All documents and the chat's attached documents, and falls back when the scoped one is removed", async () => {
    const a = card({ id: "u1", doc_id: "d1", filename: "A.pdf", status: "ready", stage: null });
    const detail = chat({
      scope: { doc_id: "d1", filename: "A.pdf" },
      messages: [
        question([
          a,
          card({ id: "u2", doc_id: "d2", filename: "B.pdf", status: "ready", stage: null, removed: true }),
          card({ id: "u3", doc_id: "d3", filename: "C.pdf", status: "failed", stage: null, error: "Not a PDF" }),
        ]),
        question([{ ...a, id: "u4" }, card({ id: "u5", doc_id: "d5", filename: "E.pdf", status: "ready", stage: null })], {
          id: "m3",
        }),
      ],
    });
    mockApi({}, { detail });
    const { queryClient } = await renderOpenChat();
    expect(scopeChip("A.pdf")).toBeInTheDocument();

    await userEvent.click(scopeChip("A.pdf"));
    const items = await screen.findAllByRole("menuitemradio");
    expect(items.map((item) => item.textContent)).toEqual(["All documents", "A.pdf", "E.pdf"]);
    expect(items[1]).toHaveAttribute("aria-checked", "true");
    await userEvent.click(items[2]);
    expect(scopeChip("E.pdf")).toBeInTheDocument();

    act(() =>
      queryClient.setQueryData<ChatDetail>(chatKeys.chat("c1"), (c) => ({
        ...c!,
        messages: c!.messages.map((m) =>
          m.role === "user" ? { ...m, attachments: m.attachments.map((x) => (x.doc_id === "d5" ? { ...x, removed: true } : x)) } : m,
        ),
      })),
    );
    expect(await screen.findByRole("button", { name: "All documents" })).toBeInTheDocument();
  });

  it("opens a ready card in the PDF panel", async () => {
    mockApi({ "POST /api/chat/uploads": card({ status: "ready", stage: null, pages: 2 }) });
    renderChat(
      <>
        <OpenChat />
        <PanelTarget />
      </>,
      { path: "/chat/c1" },
    );
    await userEvent.upload(await screen.findByLabelText("Upload files"), pdf());
    await userEvent.click(await screen.findByText("2 pages"));
    expect(screen.getByRole("status")).toHaveTextContent("d1 PROPOSAL ADDENDUM.pdf");
  });

  it("stops while an answer is generating", async () => {
    const { fetchMock, asks } = mockApi({ "POST /api/chat/chats/:id/stop": new Response(null, { status: 204 }) }, {
      detail: chat({ generating: true }),
    });
    await renderOpenChat();

    expect(screen.queryByRole("button", { name: "Send" })).not.toBeInTheDocument();
    await userEvent.type(textbox(), "Next{Enter}");
    expect(asks).toHaveLength(0);

    await userEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(calls(fetchMock, "POST", "/api/chat/chats/c1/stop")).toHaveLength(1));
  });

  it("is disabled while the library isn't ready", async () => {
    mockApi({}, { libraryReady: false });
    renderChat(<Composer chat={null} />);

    const field = await screen.findByPlaceholderText("Library isn't ready");
    expect(field).toBeDisabled();
    expect(screen.getByRole("button", { name: "Add documents" })).toBeDisabled();
    expect(sendButton()).toBeDisabled();
  });

  it("shows API errors as a toast and keeps the question", async () => {
    mockApi({ "POST /api/chat/chats/:id/messages": json({ detail: "Library isn't ready" }, 409) });
    await renderOpenChat();

    await userEvent.type(textbox(), "What fees?{Enter}");
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Library isn't ready"));
    expect(textbox()).toHaveValue("What fees?");
  });

  it("uploads PDFs dropped anywhere on the window", async () => {
    const { fetchMock } = mockApi({
      "POST /api/chat/uploads": () => card({ status: "ready", stage: null, pages: 1 }),
    });
    await renderOpenChat();

    const files = [pdf(), new File(["hello"], "notes.txt", { type: "text/plain" })];
    fireEvent.dragEnter(window, { dataTransfer: { types: ["Files"], files } });
    fireEvent.drop(window, { dataTransfer: { types: ["Files"], files } });

    expect(await screen.findByText("1 page")).toBeInTheDocument();
    expect(calls(fetchMock, "POST", "/api/chat/uploads")).toHaveLength(1);
  });
});
