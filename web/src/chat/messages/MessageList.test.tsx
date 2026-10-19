import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import { FakeEventSource } from "@/test/fake-event-source";

import {
  type AssistantMessage,
  type ChatDetail,
  chatEventsUrl,
  chatKeys,
  type Citation,
  type Passage,
  type UploadCard,
  type UserMessage,
  useChat,
} from "../api";
import { usePdfPanel } from "../pdf-panel";
import { mockFetch, renderChat } from "../test-utils";
import { MessageList } from "./MessageList";

const passage: Passage = {
  chunk_id: "c1",
  doc_id: "d1",
  seq: 3,
  text: "Retainer fee of $1,750.00 due on signing.",
  pages: [2, 3],
  regions: [{ page: 2, x0: 10, y0: 20, x1: 300, y1: 60 }],
  section: [],
  filename: "2292 Szekeres Proposal.pdf",
  job_number: "2292",
  project: "SZEKERES",
  score: 0.8,
  also_in: 0,
};

const citation: Citation = {
  n: 1,
  doc_id: "d1",
  chunk_id: "c1",
  filename: "2292 Szekeres Proposal.pdf",
  pages: [2, 3],
  regions: passage.regions,
  cited_text: "Retainer fee of $1,750.00",
  removed: false,
};

const card: UploadCard = {
  id: "u1",
  doc_id: "d9",
  filename: "PROPOSAL ADDENDUM.pdf",
  pages: 3,
  status: "ready",
  stage: null,
  error: null,
  removable: true,
  removed: false,
  job_number: null,
  project: null,
};

function question(id: string, text: string, attachments: UploadCard[] = []): UserMessage {
  return { id, role: "user", text, scope: null, created_at: "2026-10-19T10:00:00Z", attachments };
}

function answer(id: string, overrides: Partial<AssistantMessage> = {}): AssistantMessage {
  return {
    id,
    role: "assistant",
    state: "complete",
    error: null,
    scope: null,
    created_at: "2026-10-19T10:00:01Z",
    parts: [],
    search_steps: [],
    citations: [],
    ...overrides,
  };
}

function chat(messages: ChatDetail["messages"], overrides: Partial<ChatDetail> = {}): ChatDetail {
  return { id: "c", title: "Fees", scope: null, generating: false, messages, ...overrides };
}

const generatingChat = (id = "c") =>
  chat([question("q1", "What fees?"), answer("a1", { state: "generating" })], { id, generating: true });

const completeAnswer = answer("a1", {
  parts: [{ type: "text", text: "The fee is $1,750.00" }, { type: "citation", n: 1 }, { type: "text", text: "." }],
  citations: [citation],
  search_steps: [{ round: 1, tool: "search_documents", query: "fees", scope: null, passages: [passage], error: null }],
});

/** Shows the PDF panel's target so tests can check what a click opened. */
function PanelProbe() {
  const { target } = usePdfPanel();
  return <output data-testid="panel">{target ? JSON.stringify(target) : ""}</output>;
}

const panel = () => JSON.parse(screen.getByTestId("panel").textContent || "null");

/** The chat as the app shows it: from the query cache, so refetches replace it. */
function ChatView({ id }: { id: string }) {
  const { data } = useChat(id);
  return data ? <MessageList chat={data} /> : null;
}

function renderList(initial: ChatDetail, server: { chat: ChatDetail } = { chat: initial }) {
  const fetchMock = mockFetch({
    "GET /api/chat/chats/:id": () => server.chat,
    "GET /api/chat/chats": { library_ready: true, chats: [] },
  });
  const result = renderChat(
    <>
      <ChatView id={initial.id} />
      <PanelProbe />
    </>,
  );
  result.queryClient.setQueryData(chatKeys.chat(initial.id), initial);
  return { ...result, fetchMock, server };
}

function stream(chatId = "c") {
  const source = FakeEventSource.last(chatEventsUrl(chatId));
  if (!source) throw new Error("no event source");
  return {
    source,
    emit: (name: string, data: unknown) => act(() => source.emit(name, data)),
  };
}

async function emitAnswer(emit: (name: string, data: unknown) => void) {
  emit("search_started", { round: 1, tool: "search_documents", query: "fees", scope: null });
  emit("search_finished", {
    round: 1,
    tool: "search_documents",
    query: "fees",
    scope: null,
    passages: [passage],
    error: null,
  });
  emit("text_delta", { text: "The fee is " });
  emit("text_delta", { text: "$1,750.00" });
  const { removed: _, ...rest } = citation;
  emit("citation", { ...rest, source: "c1" });
  emit("text_delta", { text: " in total." });
}

test("streams text and citations in order; a citation opens the PDF at its page and regions", async () => {
  renderList(generatingChat());
  await waitFor(() => expect(FakeEventSource.last(chatEventsUrl("c"))).toBeDefined());
  const { emit } = stream();
  expect(screen.getByRole("status", { name: "Answering" })).toBeInTheDocument();

  emit("search_started", { round: 1, tool: "search_documents", query: "fees", scope: null });
  expect(screen.getByText("Searching…")).toBeInTheDocument();
  await emitAnswer((name, data) => name !== "search_started" && emit(name, data));

  const marker = await screen.findByRole("button", { name: "Citation 1" });
  const paragraph = marker.closest("p")!;
  expect(paragraph.textContent).toBe("The fee is $1,750.001 in total.");
  expect(screen.getByText("Searched all documents · 1 passage")).toBeInTheDocument();
  // No Copy or Retry while generating.
  expect(screen.queryByRole("button", { name: "Copy" })).not.toBeInTheDocument();

  await userEvent.click(marker);
  expect(panel()).toEqual({
    docId: "d1",
    filename: "2292 Szekeres Proposal.pdf",
    jobNumber: "2292",
    project: "SZEKERES",
    page: 2,
    regions: passage.regions,
  });
});

test("hovering a citation shows the document, pages and cited text", async () => {
  renderList(chat([question("q1", "What fees?"), completeAnswer]));
  await userEvent.hover(await screen.findByRole("button", { name: "Citation 1" }));
  expect(await screen.findByText("2292 Szekeres Proposal")).toBeInTheDocument();
  expect(screen.getByText("p. 2–3")).toBeInTheDocument();
  expect(screen.getByText("Retainer fee of $1,750.00")).toBeInTheDocument();
});

test("a document cited from a list opens at page 1", async () => {
  const listed: Citation = { ...citation, chunk_id: null, pages: [], regions: [], cited_text: "" };
  renderList(
    chat([
      question("q1", "Which proposals?"),
      answer("a1", { parts: [{ type: "text", text: "One" }, { type: "citation", n: 1 }], citations: [listed] }),
    ]),
  );
  await userEvent.click(await screen.findByRole("button", { name: "Citation 1" }));
  expect(panel()).toMatchObject({ docId: "d1", page: 1, regions: [] });
});

test("the search step summarizes and expands to queries and passages", async () => {
  const doc = {
    doc_id: "d2",
    paths: [],
    filename: "Grove Plaza.pdf",
    job_number: "2301",
    project: "GROVE",
    subfolder: null,
    pages: 4,
    upload: false,
    pending: false,
  };
  const scoped = answer("a1", {
    scope: { doc_id: "d1", filename: "2292 Szekeres Proposal.pdf" },
    parts: [{ type: "text", text: "Done." }],
    search_steps: [
      { round: 1, tool: "search_documents", query: "fees", scope: "d1", passages: [passage], error: null },
      {
        round: 1,
        tool: "search_documents",
        query: "retainer",
        scope: "d1",
        passages: [passage, { ...passage, chunk_id: "c2", pages: [5], regions: [] }],
        error: null,
      },
      { round: 2, tool: "read_document", query: null, scope: "d1", passages: [passage], error: null },
    ],
  });
  const listing = answer("a2", {
    parts: [{ type: "text", text: "Listed." }],
    search_steps: [{ round: 1, tool: "list_documents", query: null, scope: null, passages: [doc], error: null }],
  });
  renderList(chat([question("q1", "Fees?"), scoped, question("q2", "Which?"), listing]));

  const summary = await screen.findByRole("button", {
    name: "Searched 2292 Szekeres Proposal · 2 passages, read 2292 Szekeres Proposal",
  });
  expect(screen.getByRole("button", { name: "Listed documents · 1" })).toBeInTheDocument();
  expect(screen.queryByText("retainer")).not.toBeInTheDocument();

  await userEvent.click(summary);
  expect(screen.getByText("fees")).toBeInTheDocument();
  expect(screen.getByText("retainer")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /2292 Szekeres Proposal · p\. 5/ }));
  expect(panel()).toMatchObject({ docId: "d1", page: 5, regions: [] });
  await userEvent.click(screen.getAllByRole("button", { name: /2292 Szekeres Proposal · p\. 2–3/ })[0]);
  expect(panel()).toMatchObject({ page: 2, regions: passage.regions, jobNumber: "2292" });

  await userEvent.click(screen.getByRole("button", { name: "Listed documents · 1" }));
  await userEvent.click(screen.getByRole("button", { name: "Grove Plaza · 2301 · GROVE" }));
  expect(panel()).toMatchObject({ docId: "d2", page: 1 });
});

test("done refetches the chat and the stored answer replaces the stream without a gap", async () => {
  const server = { chat: generatingChat() };
  const { fetchMock } = renderList(server.chat, server);
  await waitFor(() => expect(FakeEventSource.last(chatEventsUrl("c"))).toBeDefined());
  const { emit, source } = stream();
  await emitAnswer(emit);

  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  fetchMock.mockImplementationOnce(async () => {
    await gate;
    return new Response(
      JSON.stringify(chat([question("q1", "What fees?"), completeAnswer])),
      { headers: { "content-type": "application/json" } },
    );
  });
  emit("done", { message_id: "a1", state: "complete", usage: {} });
  expect(source.closed).toBe(true);
  // While the refetch is in flight the streamed answer stays.
  expect(screen.getByText(/in total\./)).toBeInTheDocument();

  release();
  expect(await screen.findByText(/The fee is \$1,750\.00/)).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByText(/in total\./)).not.toBeInTheDocument());
  expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
});

test("a resubscribe replays the answer from the start without duplicating it", async () => {
  const first = renderList(generatingChat());
  await waitFor(() => expect(FakeEventSource.last(chatEventsUrl("c"))).toBeDefined());
  await emitAnswer(stream().emit);
  first.unmount();

  // Coming back: a new subscription replays every event.
  renderList(generatingChat());
  await waitFor(() => expect(FakeEventSource.instances.length).toBe(2));
  await emitAnswer(stream().emit);
  expect((await screen.findAllByRole("button", { name: "Citation 1" })).length).toBe(1);
  expect(screen.getByRole("button", { name: "Citation 1" }).closest("p")!.textContent).toBe(
    "The fee is $1,750.001 in total.",
  );
});

test("switching chats and back resubscribes with a fresh answer", async () => {
  mockFetch({ "GET /api/chat/chats/:id": (_: Request, { id }: { id: string }) => generatingChat(id) });
  let show!: (id: string) => void;
  function Switcher() {
    const [id, setId] = useState("c");
    show = setId;
    return <MessageList chat={generatingChat(id)} />;
  }
  renderChat(<Switcher />);
  await emitAnswer(stream("c").emit);
  act(() => show("other"));
  expect(screen.queryByText(/in total/)).not.toBeInTheDocument();
  act(() => show("c"));
  expect(screen.queryByText(/in total/)).not.toBeInTheDocument();
  await emitAnswer(stream("c").emit);
  expect(screen.getAllByRole("button", { name: "Citation 1" })).toHaveLength(1);
  expect(screen.getByText(/in total/).textContent).toBe("The fee is $1,750.001 in total.");
});

test("Copy copies the answer without markers; Retry shows only on the latest answer", async () => {
  const user = userEvent.setup();
  const latest = answer("a2", { state: "stopped", parts: [{ type: "text", text: "Partial" }] });
  const { fetchMock } = renderList(chat([question("q1", "What fees?"), completeAnswer, question("q2", "More?"), latest]));
  fetchMock.mockClear();
  mockFetch({
    "GET /api/chat/chats/:id": chat([question("q1", "What fees?"), completeAnswer]),
    "GET /api/chat/chats": { library_ready: true, chats: [] },
    "POST /api/chat/chats/:id/retry": { answer: answer("a3", { state: "generating" }) },
  });

  expect(await screen.findByText("Partial")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "Copy" })).toHaveLength(2);
  expect(screen.getAllByRole("button", { name: "Retry" })).toHaveLength(1);

  await user.click(screen.getAllByRole("button", { name: "Copy" })[0]);
  expect(await navigator.clipboard.readText()).toBe("The fee is $1,750.00.");

  await user.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() =>
    expect(
      (globalThis.fetch as unknown as { mock: { calls: [Request][] } }).mock.calls.some(
        ([r]) => r.method === "POST" && r.url.endsWith("/api/chat/chats/c/retry"),
      ),
    ).toBe(true),
  );
  // The new answer generates: no Retry, and its stream opens.
  await waitFor(() => expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument());
  await waitFor(() => expect(FakeEventSource.last(chatEventsUrl("c"))).toBeDefined());
});

test.each([
  ["connection", "Couldn't reach Claude."],
  ["rate_limit", "Too many requests. Try again in a moment."],
  ["api", "Something went wrong."],
  ["refusal", "Claude couldn't answer this."],
])("an %s error shows inline with Retry", async (kind, text) => {
  renderList(chat([question("q1", "What fees?"), answer("a1", { state: "error", error: kind })]));
  expect(await screen.findByText(text)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Copy" })).not.toBeInTheDocument();
});

test("an error event shows the error before the refetch", async () => {
  renderList(generatingChat());
  await waitFor(() => expect(FakeEventSource.last(chatEventsUrl("c"))).toBeDefined());
  stream().emit("error", { message_id: "a1", kind: "rate_limit" });
  expect(await screen.findByText("Too many requests. Try again in a moment.")).toBeInTheDocument();
});

test("a sent card opens from its menu and removes after confirming", async () => {
  const { fetchMock } = renderList(chat([question("q1", "About this?", [card])]));
  fetchMock.mockClear();
  mockFetch({
    "DELETE /api/chat/uploads/:id": {},
    "GET /api/chat/chats/:id": chat([question("q1", "About this?", [{ ...card, removed: true }])]),
    "GET /api/chat/chats": { library_ready: true, chats: [] },
  });

  await userEvent.click(await screen.findByRole("button", { name: "PROPOSAL ADDENDUM.pdf options" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Open" }));
  expect(panel()).toEqual({ docId: "d9", filename: "PROPOSAL ADDENDUM.pdf", jobNumber: null, project: null, page: 1 });

  await userEvent.click(screen.getByRole("button", { name: "PROPOSAL ADDENDUM.pdf options" }));
  await userEvent.click(await screen.findByRole("menuitem", { name: "Remove from library" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(within(dialog).getByText("Remove PROPOSAL ADDENDUM from the library?")).toBeInTheDocument();
  await userEvent.click(within(dialog).getByRole("button", { name: "Remove" }));
  await waitFor(() =>
    expect(
      (globalThis.fetch as unknown as { mock: { calls: [Request][] } }).mock.calls.some(
        ([r]) => r.method === "DELETE" && r.url.endsWith("/api/chat/uploads/u1"),
      ),
    ).toBe(true),
  );
  // Once removed, the menu no longer offers Remove.
  await waitFor(() => expect(screen.getByText("Removed")).toBeInTheDocument());
  await userEvent.click(screen.getByRole("button", { name: "PROPOSAL ADDENDUM.pdf options" }));
  expect(await screen.findByRole("menuitem", { name: "Open" })).toBeInTheDocument();
  expect(screen.queryByRole("menuitem", { name: "Remove from library" })).not.toBeInTheDocument();
});

test("a proposal card has no Remove", async () => {
  renderList(chat([question("q1", "About this?", [{ ...card, removable: false }])]));
  await userEvent.click(await screen.findByRole("button", { name: "PROPOSAL ADDENDUM.pdf options" }));
  expect(await screen.findByRole("menuitem", { name: "Open" })).toBeInTheDocument();
  expect(screen.queryByRole("menuitem", { name: "Remove from library" })).not.toBeInTheDocument();
});
