import type { LibraryDocument, Passage } from "../api";
import type { AnswerView } from "./useChatEvents";

/** A document's display name: its filename without ".pdf". */
export function docName(filename: string | null | undefined): string {
  if (!filename) return "Untitled document";
  return filename.replace(/\.pdf$/i, "");
}

/** [3] -> "3", [3, 4] -> "3–4", [1, 2, 3, 5] -> "1–3, 5" (as the server titles passages). */
export function compactPages(pages: number[]): string {
  const runs: [number, number][] = [];
  for (const p of [...new Set(pages)].sort((a, b) => a - b)) {
    const last = runs.at(-1);
    if (last && p === last[1] + 1) last[1] = p;
    else runs.push([p, p]);
  }
  return runs.map(([a, b]) => (a === b ? `${a}` : `${a}–${b}`)).join(", ");
}

/** "p. 3–4", or "" without pages. */
export function pagesLabel(pages: number[]): string {
  return pages.length ? `p. ${compactPages(pages)}` : "";
}

export function isPassage(item: Passage | LibraryDocument): item is Passage {
  return "chunk_id" in item;
}

/** The name of a step's scope: "all documents", or the scoped document. */
export function scopeName(scope: string | null, answer: AnswerView): string {
  if (scope == null) return "all documents";
  if (answer.scope?.doc_id === scope) return docName(answer.scope.filename);
  for (const step of answer.search_steps) {
    const found = step.passages.find((p) => p.doc_id === scope);
    if (found) return docName(found.filename);
  }
  return "the document";
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** The collapsed search-step line: "Searched all documents · 8 passages", "Read X",
 * "Listed documents · 3", combined in order of first use; "Searching…" while a search runs. */
export function stepsSummary(answer: AnswerView): string | null {
  const steps = answer.search_steps;
  if (steps.length === 0) return null;
  if (steps.some((s) => s.pending)) return "Searching…";
  const pieces: string[] = [];
  const seen = new Set<string>();
  for (const step of steps) {
    if (seen.has(step.tool)) continue;
    seen.add(step.tool);
    const same = steps.filter((s) => s.tool === step.tool);
    const distinct = new Set(same.flatMap((s) => s.passages.map((p) => (isPassage(p) ? p.chunk_id : p.doc_id))));
    if (step.tool === "search_documents") {
      pieces.push(`Searched ${scopeName(step.scope, answer)} · ${plural(distinct.size, "passage")}`);
    } else if (step.tool === "read_document") {
      pieces.push(`Read ${scopeName(step.scope, answer)}`);
    } else if (step.tool === "list_documents") {
      pieces.push(`Listed documents · ${distinct.size}`);
    }
  }
  if (pieces.length === 0) return null;
  return pieces.map((piece, i) => (i === 0 ? piece : piece[0].toLowerCase() + piece.slice(1))).join(", ");
}
