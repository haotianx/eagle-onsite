import { type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, unwrap } from "@/api/client";
import type { components } from "@/api/schema";

type Schemas = components["schemas"];

export type ChatList = Schemas["ChatList"];
export type ChatSummary = Schemas["ChatSummary"];
export type ChatDetail = Schemas["ChatDetail"];
export type UserMessage = Schemas["UserMessage"];
export type AssistantMessage = Schemas["AssistantMessage"];
export type ChatMessage = ChatDetail["messages"][number];
export type AnswerPart = AssistantMessage["parts"][number];
export type SearchStep = Schemas["SearchStep"];
export type Passage = Schemas["Passage"];
export type LibraryDocument = Schemas["LibraryDocument"];
export type Citation = Schemas["Citation"];
export type Region = Schemas["Region"];
export type Scope = Schemas["Scope"];
export type UploadCard = Schemas["UploadCard"];
export type AskResponse = Schemas["AskResponse"];

/** Error kinds of an answer (AssistantMessage.error and the `error` event). */
export type AnswerErrorKind = "connection" | "rate_limit" | "api" | "refusal";

/** Events of an answer's stream (GET /api/chat/chats/{id}/events), by event name. */
export interface ChatEventData {
  search_started: { round: number; tool: string; query: string | null; scope: string | null };
  search_finished: {
    round: number;
    tool: string;
    query: string | null;
    scope: string | null;
    passages: (Passage | LibraryDocument)[];
    error: string | null;
  };
  text_delta: { text: string };
  citation: Omit<Citation, "removed"> & { source: string };
  title: { title: string };
  done: { message_id: string; state: "complete" | "stopped"; usage: Record<string, number> };
  error: { message_id: string; kind: AnswerErrorKind };
}
export type ChatEventName = keyof ChatEventData;
export const CHAT_EVENTS: ChatEventName[] = [
  "search_started",
  "search_finished",
  "text_delta",
  "citation",
  "title",
  "done",
  "error",
];

/** Upload status snapshots (GET /api/chat/uploads/{id}/events, event "status"). */
export type UploadStatus = Pick<UploadCard, "id" | "doc_id" | "status" | "stage" | "pages" | "error">;

export const chatKeys = {
  chats: () => ["chat", "chats"] as const,
  chat: (chatId: string) => ["chat", "chats", chatId] as const,
};

export const chatEventsUrl = (chatId: string) => `/api/chat/chats/${encodeURIComponent(chatId)}/events`;
export const uploadEventsUrl = (uploadId: string) => `/api/chat/uploads/${encodeURIComponent(uploadId)}/events`;

// Chats

export function useChats() {
  return useQuery({
    queryKey: chatKeys.chats(),
    queryFn: () => unwrap(api.GET("/api/chat/chats")),
  });
}

export function useChat(chatId: string | null) {
  return useQuery({
    queryKey: chatKeys.chat(chatId ?? ""),
    queryFn: () => unwrap(api.GET("/api/chat/chats/{chat_id}", { params: { path: { chat_id: chatId! } } })),
    enabled: chatId != null,
  });
}

export function useCreateChat() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/api/chat/chats")),
    onSuccess: () => client.invalidateQueries({ queryKey: chatKeys.chats() }),
  });
}

export function useRenameChat() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ chatId, title }: { chatId: string; title: string }) =>
      unwrap(api.PATCH("/api/chat/chats/{chat_id}", { params: { path: { chat_id: chatId } }, body: { title } })),
    onSuccess: (_, { chatId }) => {
      void client.invalidateQueries({ queryKey: chatKeys.chats(), exact: true });
      void client.invalidateQueries({ queryKey: chatKeys.chat(chatId) });
    },
  });
}

export function useDeleteChat() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (chatId: string) =>
      unwrap(api.DELETE("/api/chat/chats/{chat_id}", { params: { path: { chat_id: chatId } } })),
    onSuccess: (_, chatId) => {
      client.removeQueries({ queryKey: chatKeys.chat(chatId) });
      void client.invalidateQueries({ queryKey: chatKeys.chats(), exact: true });
    },
  });
}

// Answers

export interface AskInput {
  chatId: string;
  text: string;
  /** Doc id, or null for All documents. */
  scope: string | null;
  uploadIds: string[];
}

/** Ask a question; on success the question and the generating answer are in the chat's cached
 * detail, so the answer's event stream opens right away. */
export function useAsk() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ chatId, text, scope, uploadIds }: AskInput) =>
      unwrap(
        api.POST("/api/chat/chats/{chat_id}/messages", {
          params: { path: { chat_id: chatId } },
          body: { text, scope, upload_ids: uploadIds },
        }),
      ),
    onSuccess: (response, { chatId }) => applyAsk(client, chatId, response),
  });
}

/** Regenerate the latest answer. */
export function useRetry() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (chatId: string) =>
      unwrap(api.POST("/api/chat/chats/{chat_id}/retry", { params: { path: { chat_id: chatId } } })),
    onSuccess: ({ answer }, chatId) => {
      updateChat(client, chatId, (chat) => ({
        ...chat,
        generating: true,
        messages: [...chat.messages.slice(0, -1), answer],
      }));
      void client.invalidateQueries({ queryKey: chatKeys.chats(), exact: true });
    },
  });
}

/** Stop the active answer; the stream then ends with `done {state: "stopped"}`. */
export function useStop() {
  return useMutation({
    mutationFn: (chatId: string) =>
      unwrap(api.POST("/api/chat/chats/{chat_id}/stop", { params: { path: { chat_id: chatId } } })),
  });
}

export function applyAsk(client: QueryClient, chatId: string, { question, answer }: AskResponse): void {
  updateChat(client, chatId, (chat) => ({
    ...chat,
    generating: true,
    scope: question.scope,
    messages: [...chat.messages, question, answer],
  }));
  void client.invalidateQueries({ queryKey: chatKeys.chats(), exact: true });
}

/** Refetch a chat and the sidebar list (after an answer ends, a title arrives, a removal). */
export function refreshChat(client: QueryClient, chatId: string): void {
  void client.invalidateQueries({ queryKey: chatKeys.chat(chatId) });
  void client.invalidateQueries({ queryKey: chatKeys.chats(), exact: true });
}

function updateChat(client: QueryClient, chatId: string, update: (chat: ChatDetail) => ChatDetail): void {
  client.setQueryData<ChatDetail>(chatKeys.chat(chatId), (chat) => (chat ? update(chat) : chat));
}

// Uploads

/** POST a PDF (multipart). A failed check still resolves, with status "failed" and the reason. */
export function uploadFile(file: File): Promise<UploadCard> {
  return unwrap(
    api.POST("/api/chat/uploads", {
      body: { file: file as unknown as string },
      bodySerializer: (body) => {
        const form = new FormData();
        form.append("file", body.file as unknown as File);
        return form;
      },
    }),
  );
}

/** Remove an upload's document from the library; chats that show it are refetched. */
export function useRemoveUpload() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (uploadId: string) =>
      unwrap(api.DELETE("/api/chat/uploads/{upload_id}", { params: { path: { upload_id: uploadId } } })),
    onSuccess: () => client.invalidateQueries({ queryKey: ["chat"] }),
  });
}
