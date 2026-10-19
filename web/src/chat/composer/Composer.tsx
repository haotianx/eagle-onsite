import { useRef, useState } from "react";
import { toast } from "sonner";

import {
  PromptInput,
  PromptInputAddFiles,
  PromptInputBody,
  PromptInputFooter,
  PromptInputHeader,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputTools,
} from "@/components/ai-elements/prompt-input";

import { type ChatDetail, type UploadCard, useAsk, useChats, useCreateChat, useRemoveUpload, useStop } from "../api";
import { DocumentCard } from "../DocumentCard";
import { usePdfPanel } from "../pdf-panel";
import { navigateToChat } from "../route";
import { ScopeChip, scopeOptions } from "./ScopeChip";
import { isPdf, type UnsentCard, useUpload, useUploadEvents } from "./useUpload";
import { useWindowDrop } from "./useWindowDrop";

export interface ComposerProps {
  /** The open chat, or null for a new chat (created on the first send). */
  chat: ChatDetail | null;
  className?: string;
}

/** The question box: unsent document cards, the text field, `+`, the scope chip and send / stop.
 * Its draft (text, cards, scope) belongs to one chat. */
export function Composer(props: ComposerProps) {
  return <ChatComposer key={props.chat?.id ?? ""} {...props} />;
}

function ChatComposer({ chat, className }: ComposerProps) {
  const libraryReady = useChats().data?.library_ready !== false;
  const createChat = useCreateChat();
  const ask = useAsk();
  const stop = useStop();
  const removeUpload = useRemoveUpload();
  const pdf = usePdfPanel();

  const [text, setText] = useState("");
  const [scope, setScope] = useState<string | null>(chat?.scope?.doc_id ?? null);
  const upload = useUpload({ onReady: (card) => setScope(card.doc_id) });
  // A chat created by a send whose question then failed; the next send reuses it.
  const createdChatId = useRef<string | null>(null);

  const options = scopeOptions(chat, upload.cards);
  const effectiveScope = options.some((o) => o.docId === scope) ? scope : null;

  const generating = chat?.generating ?? false;
  const sending = createChat.isPending || ask.isPending;
  const processing = upload.cards.some((u) => u.card.status === "processing");
  const canSend = libraryReady && !generating && !sending && !processing && text.trim() !== "";

  const addFiles = (files: File[]) => {
    const pdfs = files.filter(isPdf);
    if (pdfs.length > 0) upload.add(pdfs);
  };
  const dragging = useWindowDrop(addFiles, libraryReady);

  const send = async () => {
    if (!canSend) return;
    const sent = upload.cards.filter((u) => !u.pending && u.card.status === "ready");
    try {
      let chatId = chat?.id ?? createdChatId.current;
      if (chatId == null) {
        chatId = (await createChat.mutateAsync()).id;
        createdChatId.current = chatId;
      }
      await ask.mutateAsync({
        chatId,
        text: text.trim(),
        scope: effectiveScope,
        uploadIds: sent.map((u) => u.card.id),
      });
      setText("");
      upload.drop(sent.map((u) => u.key));
      if (chat == null) navigateToChat(chatId);
    } catch (error) {
      toast.error(errorText(error));
    }
  };

  const remove = (u: UnsentCard) => {
    if (!u.card.removable) {
      upload.drop([u.key]);
      return;
    }
    removeUpload.mutate(u.card.id, {
      onSuccess: () => upload.drop([u.key]),
      onError: (error) => toast.error(errorText(error)),
    });
  };

  const open = (card: UploadCard) =>
    pdf.open({ docId: card.doc_id, filename: card.filename, jobNumber: card.job_number, project: card.project });

  return (
    <>
      <PromptInput
        accept="application/pdf,.pdf"
        className={className}
        disabled={!libraryReady}
        multiple
        onFiles={addFiles}
        onSubmit={() => void send()}
      >
        {upload.cards.length > 0 && (
          <PromptInputHeader className="gap-2 px-3 pt-3">
            {upload.cards.map((u) => (
              <ComposerCard
                key={u.key}
                upload={u}
                onStatus={(status) => upload.applyStatus(u.key, u.card, status)}
                onOpen={() => open(u.card)}
                onRemove={u.pending ? undefined : () => remove(u)}
              />
            ))}
          </PromptInputHeader>
        )}
        <PromptInputBody>
          <PromptInputTextarea
            className="px-4 pt-3"
            disabled={!libraryReady}
            onChange={(e) => setText(e.currentTarget.value)}
            placeholder={libraryReady ? "Ask a question" : "Library isn't ready"}
            value={text}
          />
        </PromptInputBody>
        <PromptInputFooter className="px-2 pb-2">
          <PromptInputTools>
            <PromptInputAddFiles aria-label="Add documents" />
            <ScopeChip options={options} value={effectiveScope} onChange={setScope} disabled={!libraryReady} />
          </PromptInputTools>
          {generating && chat ? (
            <PromptInputSubmit
              aria-label="Stop"
              className="rounded-full"
              onClick={() => stop.mutate(chat.id, { onError: (error) => toast.error(errorText(error)) })}
              status="streaming"
              type="button"
            />
          ) : (
            <PromptInputSubmit aria-label="Send" className="rounded-full" disabled={!canSend} />
          )}
        </PromptInputFooter>
      </PromptInput>
      {dragging && (
        <div aria-hidden className="pointer-events-none fixed inset-0 z-50 bg-primary/5 ring-2 ring-primary/30 ring-inset" />
      )}
    </>
  );
}

interface ComposerCardProps {
  upload: UnsentCard;
  onStatus: Parameters<typeof useUploadEvents>[1];
  onOpen(): void;
  onRemove?(): void;
}

function ComposerCard({ upload, onStatus, onOpen, onRemove }: ComposerCardProps) {
  useUploadEvents(upload, onStatus);
  return <DocumentCard card={upload.card} onOpen={onOpen} onRemove={onRemove} />;
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong";
}
