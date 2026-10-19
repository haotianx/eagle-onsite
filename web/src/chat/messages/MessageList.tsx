import { Conversation, ConversationContent, ConversationScrollButton } from "@/components/ai-elements/conversation";

import type { ChatDetail } from "../api";
import { Answer } from "./Answer";
import { Question } from "./Question";
import { useChatEvents } from "./useChatEvents";

export interface MessageListProps {
  chat: ChatDetail;
}

/** A chat's questions and answers; the generating answer streams in. Sticks to the bottom while
 * the user hasn't scrolled up. */
export function MessageList({ chat }: MessageListProps) {
  const streaming = useChatEvents(chat);
  const last = chat.messages.length - 1;
  return (
    <Conversation className="size-full min-h-0">
      <ConversationContent className="mx-auto w-full max-w-3xl gap-6 px-4 py-6">
        {chat.messages.map((message, i) =>
          message.role === "user" ? (
            <Question key={message.id} message={message} />
          ) : (
            <Answer
              key={message.id}
              chatId={chat.id}
              answer={i === last && streaming ? streaming : message}
              retryable={i === last && !chat.generating}
            />
          ),
        )}
      </ConversationContent>
      <ConversationScrollButton />
    </Conversation>
  );
}
