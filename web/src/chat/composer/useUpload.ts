import { useCallback, useEffect, useRef, useState } from "react";

import { useEventSource } from "@/api/sse";

import { type UploadCard, type UploadStatus, uploadEventsUrl, uploadFile } from "../api";

/** A card in the composer, not yet sent. `pending` while its POST is in flight (no upload id yet). */
export interface UnsentCard {
  key: string;
  card: UploadCard;
  pending: boolean;
}

let nextKey = 0;

function placeholder(file: File): UploadCard {
  return {
    id: "",
    doc_id: "",
    filename: file.name,
    pages: null,
    status: "processing",
    stage: null,
    error: null,
    removable: false,
    removed: false,
    job_number: null,
    project: null,
  };
}

export function isPdf(file: File): boolean {
  return file.type === "application/pdf" || /\.pdf$/i.test(file.name);
}

/** The composer's unsent cards. `add` posts each file and shows its card right away; each card
 * follows its status with useUploadEvents and reports snapshots to `applyStatus`. `onReady` runs
 * when a card becomes ready. */
export function useUpload({ onReady }: { onReady(card: UploadCard): void }) {
  const [cards, setCards] = useState<UnsentCard[]>([]);
  const readyHandler = useRef(onReady);
  useEffect(() => {
    readyHandler.current = onReady;
  });

  const set = useCallback((key: string, card: UploadCard) => {
    setCards((prev) => prev.map((u) => (u.key === key ? { key, card, pending: false } : u)));
    if (card.status === "ready") readyHandler.current(card);
  }, []);

  const add = useCallback(
    (files: File[]) => {
      for (const file of files) {
        const key = `upload-${nextKey++}`;
        const initial = placeholder(file);
        setCards((prev) => [...prev, { key, card: initial, pending: true }]);
        uploadFile(file).then(
          (card) => set(key, card),
          (error: unknown) =>
            set(key, { ...initial, status: "failed", error: error instanceof Error ? error.message : "Upload failed" }),
        );
      }
    },
    [set],
  );

  const applyStatus = useCallback(
    (key: string, card: UploadCard, status: UploadStatus) => set(key, { ...card, ...status }),
    [set],
  );

  const drop = useCallback((keys: string[]) => {
    setCards((prev) => prev.filter((u) => !keys.includes(u.key)));
  }, []);

  return { cards, add, applyStatus, drop };
}

const STATUS_EVENTS = { events: ["status"] };

/** Follows a processing card's status snapshots; stops listening once it is ready or failed. */
export function useUploadEvents({ card, pending }: UnsentCard, onStatus: (status: UploadStatus) => void): void {
  const following = !pending && card.status === "processing";
  useEventSource(following ? uploadEventsUrl(card.id) : null, (_, data) => onStatus(data as UploadStatus), STATUS_EVENTS);
}
