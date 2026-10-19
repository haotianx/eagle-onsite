import { createContext, type ReactNode, useContext, useMemo } from "react";

import {
  InlineCitation,
  InlineCitationCard,
  InlineCitationCardBody,
  InlineCitationCardTrigger,
  InlineCitationQuote,
  InlineCitationSource,
} from "@/components/ai-elements/inline-citation";

import type { Citation } from "../api";
import { usePdfPanel } from "../pdf-panel";
import { docName, pagesLabel } from "./format";
import type { AnswerView } from "./useChatEvents";

interface CitationsValue {
  byN: Map<number, Citation>;
  open(citation: Citation): void;
}

const CitationsContext = createContext<CitationsValue | null>(null);

/** Gives an answer's citation markers their citations. Context rather than props, since the
 * markdown renderer only re-renders on new text. */
export function CitationsProvider({ answer, children }: { answer: AnswerView; children: ReactNode }) {
  const pdf = usePdfPanel();
  const value = useMemo<CitationsValue>(
    () => ({
      byN: new Map(answer.citations.map((c) => [c.n, c])),
      open(citation) {
        const doc = answer.search_steps.flatMap((s) => s.passages).find((p) => p.doc_id === citation.doc_id);
        pdf.open({
          docId: citation.doc_id,
          filename: citation.filename,
          jobNumber: doc?.job_number,
          project: doc?.project,
          page: citation.pages[0] ?? 1,
          regions: citation.regions,
        });
      },
    }),
    [answer.citations, answer.search_steps, pdf],
  );
  return <CitationsContext.Provider value={value}>{children}</CitationsContext.Provider>;
}

/** A citation marker in the answer (the <citation n> element of remarkCitations). */
export function CitationMarker(props: Record<string, unknown>) {
  const value = useContext(CitationsContext);
  const n = Number(props.n);
  const citation = value?.byN.get(n);
  if (!value || !citation) return null;
  const pages = pagesLabel(citation.pages);
  return (
    <InlineCitation>
      <InlineCitationCard>
        <InlineCitationCardTrigger aria-label={`Citation ${n}`} onClick={() => value.open(citation)}>
          {n}
        </InlineCitationCardTrigger>
        <InlineCitationCardBody className="space-y-2 p-3">
          <InlineCitationSource title={docName(citation.filename)}>
            {pages && <p className="text-xs text-muted-foreground">{pages}</p>}
          </InlineCitationSource>
          {citation.cited_text && (
            <InlineCitationQuote className="line-clamp-4 not-italic">{citation.cited_text}</InlineCitationQuote>
          )}
        </InlineCitationCardBody>
      </InlineCitationCard>
    </InlineCitation>
  );
}
