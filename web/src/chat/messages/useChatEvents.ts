import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { useEventSource } from "@/api/sse";

import {
  type AnswerErrorKind,
  type AnswerPart,
  type AssistantMessage,
  CHAT_EVENTS,
  type ChatDetail,
  type ChatEventData,
  chatEventsUrl,
  type Citation,
  refreshChat,
  type SearchStep,
} from "../api";

/** A search step of an answer; `pending` while its search hasn't finished. */
export type AnswerStep = SearchStep & { pending?: boolean };

/** An answer as shown: a stored message, or the streaming one with its steps so far. */
export type AnswerView = Omit<AssistantMessage, "search_steps"> & { search_steps: AnswerStep[] };

interface Stream {
  key: string | null;
  parts: AnswerPart[];
  citations: Citation[];
  steps: AnswerStep[];
  state: AssistantMessage["state"];
  error: AnswerErrorKind | null;
}

const CLOSE_ON = ["done", "error"];

function emptyStream(key: string | null): Stream {
  return { key, parts: [], citations: [], steps: [], state: "generating", error: null };
}

/** Follows the chat's generating answer over SSE and returns it as built so far, or null when
 * nothing is generating. A (re)subscription replays the answer from its first event, so the state
 * starts empty on each. When the answer ends the chat is refetched; the streamed state stays until
 * the stored message replaces it. */
export function useChatEvents(chat: ChatDetail): AnswerView | null {
  const client = useQueryClient();
  const last = chat.messages.at(-1);
  const answer = chat.generating && last?.role === "assistant" && last.state === "generating" ? last : null;
  const key = answer ? `${chat.id}/${answer.id}` : null;

  const [stream, setStream] = useState<Stream>(() => emptyStream(key));

  // Before the subscription below opens (effects run in order), so a reopened stream's replay
  // starts from nothing.
  useEffect(() => {
    setStream(emptyStream(key));
  }, [key]);

  useEventSource(
    answer ? chatEventsUrl(chat.id) : null,
    (name, data) => {
      if (name === "title") {
        refreshChat(client, chat.id);
        return;
      }
      if (name === "done" || name === "error") refreshChat(client, chat.id);
      setStream((prev) => apply(prev.key === key ? prev : emptyStream(key), name, data));
    },
    { events: CHAT_EVENTS, closeOn: CLOSE_ON },
  );

  if (!answer) return null;
  const current = stream.key === key ? stream : emptyStream(key);
  return {
    ...answer,
    state: current.state,
    error: current.error,
    parts: current.parts,
    citations: current.citations,
    search_steps: current.steps,
  };
}

function apply(stream: Stream, name: string, data: unknown): Stream {
  switch (name) {
    case "text_delta": {
      const { text } = data as ChatEventData["text_delta"];
      const lastPart = stream.parts.at(-1);
      const parts =
        lastPart?.type === "text"
          ? [...stream.parts.slice(0, -1), { type: "text" as const, text: lastPart.text + text }]
          : [...stream.parts, { type: "text" as const, text }];
      return { ...stream, parts };
    }
    case "citation": {
      const { source: _source, ...citation } = data as ChatEventData["citation"];
      const known = stream.citations.some((c) => c.n === citation.n);
      return {
        ...stream,
        parts: [...stream.parts, { type: "citation", n: citation.n }],
        citations: known ? stream.citations : [...stream.citations, { ...citation, removed: false }],
      };
    }
    case "search_started": {
      const step = data as ChatEventData["search_started"];
      return { ...stream, steps: [...stream.steps, { ...step, passages: [], error: null, pending: true }] };
    }
    case "search_finished": {
      const step = data as ChatEventData["search_finished"];
      const i = stream.steps.findIndex(
        (s) => s.pending && s.round === step.round && s.tool === step.tool && s.query === step.query,
      );
      const steps = i < 0 ? [...stream.steps, step] : stream.steps.map((s, j) => (j === i ? step : s));
      return { ...stream, steps };
    }
    case "done":
      return { ...stream, state: (data as ChatEventData["done"]).state };
    case "error": {
      // A connection error (no data) leaves the answer as it is; the refetch tells what happened.
      const kind = (data as Partial<ChatEventData["error"]> | undefined)?.kind;
      return kind ? { ...stream, state: "error", error: kind, steps: stream.steps.filter((s) => !s.pending) } : stream;
    }
    default:
      return stream;
  }
}
