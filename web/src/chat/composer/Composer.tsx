import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
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

import {
  type ChatDetail,
  chatKeys,
  type UploadCard,
  useAsk,
  useChats,
  useCreateChat,
  useRemoveUpload,
  useStop,
} from "../api";
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
 * Its draft (text, cards, scope) belongs to one chat: it resets when another chat opens, but not
 * when a new chat opens as the chat its own send created. */
export function Composer(props: ComposerProps) {
  const chatId = props.chat?.id ?? null;
  const [draft, setDraft] = useState<{ chatId: string | null; key: number; created: string | null }>({
    chatId,
    key: 0,
    created: null,
  });
  if (draft.chatId !== chatId) {
    const adopted = draft.chatId == null && chatId != null && chatId === draft.created;
    setDraft({ chatId, key: adopted ? draft.key : draft.key + 1, created: null });
  }
  return (
    <ChatComposer
      key={draft.key}
      created={draft.created}
      onCreated={(id) => setDraft((d) => ({ ...d, created: id }))}
      {...props}
    />
  );
}

interface ChatComposerProps extends ComposerProps {
  /** The chat this new chat's send created (kept if its question then failed, for the next send). */
  created: string | null;
  onCreated(chatId: string): void;
}

function ChatComposer({ chat, className, created, onCreated }: ChatComposerProps) {
  const queryClient = useQueryClient();
  const libraryReady = useChats().data?.library_ready !== false;
  const createChat = useCreateChat();
  const ask = useAsk();
  const stop = useStop();
  const removeUpload = useRemoveUpload();
  const pdf = usePdfPanel();

  const [text, setText] = useState("");
  const [scope, setScope] = useState<string | null>(chat?.scope?.doc_id ?? null);
  const upload = useUpload({ onReady: (card) => setScope(card.doc_id) });

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
      let chatId = chat?.id ?? created;
      if (chatId == null) {
        chatId = (await createChat.mutateAsync()).id;
        onCreated(chatId);
        // Seeded so the question and the generating answer land in it and the chat shows at once.
        queryClient.setQueryData<ChatDetail>(chatKeys.chat(chatId), {
          id: chatId,
          title: null,
          scope: null,
          generating: false,
          messages: [],
        });
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
