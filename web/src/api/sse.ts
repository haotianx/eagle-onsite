import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { queryKeys } from "./hooks";

export interface EventSourceOptions {
  /** Named SSE events to listen for. */
  events: string[];
  /** Events after which the stream is closed (so EventSource doesn't reconnect to a finished stream). */
  closeOn?: string[];
}

/** Subscribes to an SSE stream while `url` is non-null. `data` is parsed as JSON (the raw string if
 * it isn't JSON). Closes on unmount, when `url` changes, and after any `closeOn` event. */
export function useEventSource(
  url: string | null,
  onEvent: (name: string, data: unknown, event: MessageEvent) => void,
  { events, closeOn = [] }: EventSourceOptions,
): void {
  const handler = useRef(onEvent);
  useEffect(() => {
    handler.current = onEvent;
  });

  const eventsKey = events.join("\n");
  const closeKey = closeOn.join("\n");

  useEffect(() => {
    if (url == null) return;
    const source = new EventSource(url);
    const closing = new Set(closeKey ? closeKey.split("\n") : []);
    const listeners = (eventsKey ? eventsKey.split("\n") : []).map((name) => {
      const listener = (event: Event) => {
        const message = event as MessageEvent<string>;
        handler.current(name, parse(message.data), message);
        if (closing.has(name)) source.close();
      };
      source.addEventListener(name, listener);
      return [name, listener] as const;
    });
    return () => {
      for (const [name, listener] of listeners) source.removeEventListener(name, listener);
      source.close();
    };
  }, [url, eventsKey, closeKey]);
}

function parse(data: string): unknown {
  try {
    return JSON.parse(data);
  } catch {
    return data;
  }
}

const REPO_EVENTS = { events: ["repo_changed"] };

/** Refreshes DAG queries (states, dirty badges) whenever the repo's DAG, node or checks files change. */
export function useRepoEvents(): void {
  const client = useQueryClient();
  useEventSource(
    "/api/events",
    () => {
      void client.invalidateQueries({ queryKey: queryKeys.dags() });
    },
    REPO_EVENTS,
  );
}
