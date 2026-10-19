import type { ChatDetail } from "../api";

export interface ComposerProps {
  /** The open chat, or null for a new chat (created on the first send). */
  chat: ChatDetail | null;
  className?: string;
}

/** Placeholder: the composer component replaces this file. */
export function Composer(_props: ComposerProps) {
  return null;
}
