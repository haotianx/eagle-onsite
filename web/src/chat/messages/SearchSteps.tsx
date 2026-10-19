import { FileTextIcon, ListIcon, SearchIcon } from "lucide-react";
import type { ReactNode } from "react";

import { Tool, ToolContent, ToolHeader } from "@/components/ai-elements/tool";

import type { LibraryDocument, Passage } from "../api";
import { type PdfTarget, usePdfPanel } from "../pdf-panel";
import { docName, isPassage, pagesLabel, scopeName } from "./format";
import type { AnswerStep, AnswerView } from "./useChatEvents";

/** The collapsed search-step line above an answer; expands to each search and what it found. */
export function SearchSteps({ answer, summary }: { answer: AnswerView; summary: string }) {
  return (
    <Tool>
      <ToolHeader title={summary} running={answer.search_steps.some((s) => s.pending)} />
      <ToolContent>
        <div className="mt-2 ml-2 space-y-4 border-l pl-4">
          {answer.search_steps.map((step, i) => (
            <Step key={i} step={step} answer={answer} />
          ))}
        </div>
      </ToolContent>
    </Tool>
  );
}

function Step({ step, answer }: { step: AnswerStep; answer: AnswerView }) {
  const pdf = usePdfPanel();
  if (step.tool === "read_document") {
    const doc = step.passages[0];
    return (
      <StepTitle icon={<FileTextIcon />}>
        {doc ? (
          <button type="button" className="text-left hover:underline" onClick={() => pdf.open(docTarget(doc))}>
            Read {scopeName(step.scope, answer)}
          </button>
        ) : (
          <>Read {scopeName(step.scope, answer)}</>
        )}
      </StepTitle>
    );
  }
  const list = step.tool === "list_documents";
  return (
    <div className="space-y-1.5">
      <StepTitle icon={list ? <ListIcon /> : <SearchIcon />}>{list ? "Listed documents" : step.query}</StepTitle>
      {step.passages.length > 0 && (
        <ul className="space-y-0.5">
          {step.passages.map((item) => (
            <li key={isPassage(item) ? item.chunk_id : item.doc_id}>
              <button
                type="button"
                className="w-full rounded-md px-2 py-1.5 text-left hover:bg-accent"
                onClick={() => pdf.open(target(item))}
              >
                <div className="truncate text-xs font-medium">{itemTitle(item)}</div>
                {isPassage(item) && <div className="line-clamp-2 text-xs text-muted-foreground">{item.text}</div>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function StepTitle({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-center gap-2 text-sm text-muted-foreground [&_svg]:size-3.5 [&_svg]:shrink-0">
      {icon}
      <span className="min-w-0">{children}</span>
    </div>
  );
}

function itemTitle(item: Passage | LibraryDocument): string {
  const parts = isPassage(item)
    ? [docName(item.filename), pagesLabel(item.pages)]
    : [docName(item.filename), item.job_number, item.project];
  return parts.filter(Boolean).join(" · ");
}

/** A document opens at page 1. */
function docTarget(item: Passage | LibraryDocument): PdfTarget {
  return { docId: item.doc_id, filename: item.filename, jobNumber: item.job_number, project: item.project, page: 1 };
}

/** A passage opens at its first page with its regions. */
function target(item: Passage | LibraryDocument): PdfTarget {
  if (!isPassage(item)) return docTarget(item);
  return { ...docTarget(item), page: item.pages[0] ?? 1, regions: item.regions };
}
