import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { PdfViewerProps } from "@/components/pdf/PdfViewer";

import type { ChatDetail, ChatMessage } from "./api";
import { ChatLayout } from "./ChatLayout";
import { type PdfTarget, usePdfPanel } from "./pdf-panel";
import { navigateToChat } from "./route";
import { mockFetch, renderChat } from "./test-utils";

const SZEKERES: PdfTarget = {
  docId: "d1",
  filename: "2292 Szekeres Proposal.pdf",
  jobNumber: "2292",
  project: "SZEKERES",
  page: 2,
  regions: [{ page: 2, x0: 72, y0: 100, x1: 300, y1: 140 }],
};

vi.mock("./messages/MessageList", () => ({
  MessageList: function MessageList() {
    const { open } = usePdfPanel();
    return (
      <>
        <button onClick={() => open(SZEKERES)}>Open citation</button>
        <button onClick={() => open({ docId: "d2", page: 3 })}>Open card</button>
      </>
    );
  },
}));
vi.mock("./composer/Composer", () => ({
  Composer: function Composer() {
    const { open } = usePdfPanel();
    return <button onClick={() => open({ docId: "d3" })}>Open upload</button>;
  },
}));
vi.mock("@/components/pdf/PdfViewer", () => ({
  PdfViewer: ({ docId, page, highlights, onPageChange }: PdfViewerProps) => (
    <div data-testid="viewer" data-doc={docId} data-page={page} data-highlights={highlights?.length ?? 0}>
      <button onClick={() => onPageChange?.(2, 5)}>Scroll</button>
    </div>
  ),
}));

const chat = (id: string, title: string | null): ChatDetail => ({
  id,
  title,
  generating: false,
  scope: null,
  messages: [{ id: "m1", role: "user", text: "Fees?" } as unknown as ChatMessage],
});

function mockApi() {
  mockFetch({
    "GET /api/chat/chats": { chats: [], library_ready: true },
    "GET /api/chat/chats/:id": (_: Request, { id }: Record<string, string>) =>
      chat(id, id === "c1" ? "Szekeres fees" : null),
  });
}

test("the document title follows the open chat", async () => {
  mockApi();
  renderChat(<ChatLayout />, { path: "/chat/c1" });
  await vi.waitFor(() => expect(document.title).toBe("Szekeres fees"));
  act(() => navigateToChat("c2"));
  await vi.waitFor(() => expect(document.title).toBe("Chat"));
  act(() => navigateToChat(null));
  expect(document.title).toBe("Chat");
});

test("the PDF panel opens at the target and closes with ✕", async () => {
  mockApi();
  renderChat(<ChatLayout />, { path: "/chat/c1" });
  expect(screen.queryByTestId("viewer")).not.toBeInTheDocument();

  await userEvent.click(await screen.findByRole("button", { name: "Open citation" }));
  const viewer = screen.getByTestId("viewer");
  expect(viewer).toHaveAttribute("data-doc", "d1");
  expect(viewer).toHaveAttribute("data-page", "2");
  expect(viewer).toHaveAttribute("data-highlights", "1");
  expect(screen.getByText("2292 Szekeres Proposal.pdf")).toBeInTheDocument();
  expect(screen.getByText("2292 · SZEKERES")).toBeInTheDocument();
  expect(screen.queryByText(/^p\. /)).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Scroll" }));
  expect(screen.getByText("p. 2 / 5")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Open card" }));
  expect(screen.getByTestId("viewer")).toHaveAttribute("data-doc", "d2");
  expect(screen.getByTestId("viewer")).toHaveAttribute("data-page", "3");
  expect(screen.getByText("Document")).toBeInTheDocument();
  expect(screen.queryByText("p. 2 / 5")).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(screen.queryByTestId("viewer")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Open citation" })).toBeInTheDocument();
});

test("switching chats closes the PDF panel", async () => {
  mockApi();
  renderChat(<ChatLayout />, { path: "/chat/c1" });
  await userEvent.click(await screen.findByRole("button", { name: "Open citation" }));
  expect(screen.getByTestId("viewer")).toBeInTheDocument();

  act(() => navigateToChat("c2"));
  expect(screen.queryByTestId("viewer")).not.toBeInTheDocument();
});

test("a new chat getting its id keeps the PDF panel open", async () => {
  mockApi();
  renderChat(<ChatLayout />, { path: "/chat" });
  await userEvent.click(await screen.findByRole("button", { name: "Open upload" }));
  expect(screen.getByTestId("viewer")).toHaveAttribute("data-doc", "d3");

  act(() => navigateToChat("c1"));
  expect(await screen.findByRole("heading", { name: "Szekeres fees" })).toBeInTheDocument();
  expect(screen.getByTestId("viewer")).toHaveAttribute("data-doc", "d3");
});
