import { CheckIcon, CopyIcon, RotateCcwIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { defaultRemarkPlugins } from "streamdown";

import { Message, MessageAction, MessageActions, MessageContent, MessageResponse } from "@/components/ai-elements/message";

import { type AnswerErrorKind, useRetry } from "../api";
import { CitationMarker, CitationsProvider } from "./Citations";
import { stepsSummary } from "./format";
import { answerMarkdown, answerText, CITATION_TAG, remarkCitations } from "./markers";
import { SearchSteps } from "./SearchSteps";
import type { AnswerView } from "./useChatEvents";

const REMARK_PLUGINS = [...Object.values(defaultRemarkPlugins), remarkCitations];
const ALLOWED_TAGS = { [CITATION_TAG]: ["n"] };
const COMPONENTS = { [CITATION_TAG]: CitationMarker };

const ERRORS: Record<AnswerErrorKind, string> = {
  connection: "Couldn't reach Claude.",
  rate_limit: "Too many requests. Try again in a moment.",
  api: "Something went wrong.",
  refusal: "Claude couldn't answer this.",
};

export interface AnswerProps {
  chatId: string;
  answer: AnswerView;
  /** The chat's latest answer, and nothing is generating: it can be retried. */
  retryable: boolean;
}

/** An answer: the search step, the markdown with citations, then Copy and Retry or an error. */
export function Answer({ chatId, answer, retryable }: AnswerProps) {
  const markdown = useMemo(() => answerMarkdown(answer.parts), [answer.parts]);
  const summary = stepsSummary(answer);
  const generating = answer.state === "generating";
  const finished = answer.state === "complete" || answer.state === "stopped";

  return (
    <Message from="assistant" className="max-w-full">
      {summary && <SearchSteps answer={answer} summary={summary} />}
      {markdown.trim() ? (
        <MessageContent className="w-full">
          <CitationsProvider answer={answer}>
            <MessageResponse
              isAnimating={generating}
              remarkPlugins={REMARK_PLUGINS}
              allowedTags={ALLOWED_TAGS}
              components={COMPONENTS}
            >
              {markdown}
            </MessageResponse>
          </CitationsProvider>
        </MessageContent>
      ) : (
        generating && !summary && <span role="status" aria-label="Answering" className="size-2.5 animate-pulse rounded-full bg-foreground/60" />
      )}
      {answer.state === "error" && (
        <div className="flex items-center gap-2 text-sm text-destructive">
          {ERRORS[(answer.error as AnswerErrorKind | null) ?? "api"] ?? ERRORS.api}
          {retryable && <RetryButton chatId={chatId} />}
        </div>
      )}
      {finished && (
        <MessageActions className="-ml-2">
          {markdown.trim() && <CopyButton text={answerText(answer.parts)} />}
          {retryable && <RetryButton chatId={chatId} />}
        </MessageActions>
      )}
    </Message>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);
  return (
    <MessageAction
      tooltip="Copy"
      onClick={() => {
        void navigator.clipboard.writeText(text).then(() => setCopied(true));
      }}
    >
      {copied ? <CheckIcon /> : <CopyIcon />}
    </MessageAction>
  );
}

function RetryButton({ chatId }: { chatId: string }) {
  const retry = useRetry();
  return (
    <MessageAction tooltip="Retry" disabled={retry.isPending} onClick={() => retry.mutate(chatId)}>
      <RotateCcwIcon />
    </MessageAction>
  );
}
