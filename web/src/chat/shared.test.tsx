import { act, renderHook, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { UploadCard } from "./api";
import { cardStatus, DocumentCard } from "./DocumentCard";
import { navigateToChat, useChatId } from "./route";
import { renderChat } from "./test-utils";

const card: UploadCard = {
  id: "u1",
  doc_id: "d1",
  filename: "PROPOSAL ADDENDUM.pdf",
  pages: 3,
  status: "processing",
  stage: "reading",
  error: null,
  removable: true,
  removed: false,
  job_number: null,
  project: null,
};

test("card status follows the upload's stage", () => {
  expect(cardStatus(card)).toBe("Reading 3 pages…");
  expect(cardStatus({ ...card, stage: "queued" })).toBe("Waiting…");
  expect(cardStatus({ ...card, stage: "indexing" })).toBe("Indexing…");
  expect(cardStatus({ ...card, status: "failed", stage: null, error: "Encrypted" })).toBe("Encrypted");
  expect(cardStatus({ ...card, status: "ready", stage: null, job_number: "2292", project: "SZEKERES" })).toBe(
    "2292 · SZEKERES",
  );
  expect(cardStatus({ ...card, status: "ready", stage: null })).toBe("3 pages");
});

test("a ready card opens and an unsent card removes", async () => {
  const onOpen = vi.fn();
  const onRemove = vi.fn();
  renderChat(<DocumentCard card={{ ...card, status: "ready", stage: null }} onOpen={onOpen} onRemove={onRemove} />);
  await userEvent.click(screen.getByText("PROPOSAL ADDENDUM.pdf"));
  expect(onOpen).toHaveBeenCalledOnce();
  await userEvent.click(screen.getByRole("button", { name: "Remove PROPOSAL ADDENDUM.pdf" }));
  expect(onRemove).toHaveBeenCalledOnce();
  expect(onOpen).toHaveBeenCalledOnce();
});

test("the chat id follows the URL", () => {
  window.history.replaceState(null, "", "/chat");
  const { result } = renderHook(() => useChatId());
  expect(result.current).toBeNull();
  act(() => navigateToChat("abc"));
  expect(result.current).toBe("abc");
  expect(window.location.pathname).toBe("/chat/abc");
  act(() => navigateToChat(null, { replace: true }));
  expect(result.current).toBeNull();
});
