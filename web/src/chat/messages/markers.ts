import type { AnswerPart } from "../api";

/** A citation marker in an answer's markdown: private-use characters around the number, so it
 * can't collide with the answer's own text or markdown syntax. remarkCitations turns each into a
 * <citation n> element, which the answer renders as an InlineCitation. */
const OPEN = "";
const CLOSE = "";
const MARKER = /(\d+)/g;

export const CITATION_TAG = "citation";

export function marker(n: number): string {
  return `${OPEN}${n}${CLOSE}`;
}

/** The answer's markdown with a marker where each citation part sits. A marker that would follow
 * a block's trailing whitespace goes before it, so it stays on the cited line; a repeated marker
 * right after the same one is dropped. */
export function answerMarkdown(parts: AnswerPart[]): string {
  let markdown = "";
  let lastN: number | null = null;
  for (const part of parts) {
    if (part.type === "text") {
      markdown += part.text;
      if (part.text) lastN = null;
      continue;
    }
    if (part.n === lastN) continue;
    lastN = part.n;
    const at = markerPosition(markdown);
    markdown = markdown.slice(0, at) + marker(part.n) + markdown.slice(at);
  }
  return markdown;
}

/** Where a marker after `markdown` goes: before trailing whitespace, and inside the last cell when
 * the text ends with a table row (text after a row's closing pipe is dropped). */
function markerPosition(markdown: string): number {
  const body = markdown.trimEnd();
  const line = body.slice(body.lastIndexOf("\n") + 1);
  if (line.trimStart().startsWith("|") && line.length > 1 && line.endsWith("|")) {
    return body.slice(0, -1).trimEnd().length;
  }
  return body.length;
}

/** The answer's text without markers (Copy). */
export function answerText(parts: AnswerPart[]): string {
  return parts
    .map((part) => (part.type === "text" ? part.text : ""))
    .join("")
    .trim();
}

// The few mdast shapes the plugin touches.
interface MdNode {
  type: string;
  value?: string;
  children?: MdNode[];
  data?: { hName?: string; hProperties?: Record<string, string> };
}

/** Remark plugin: splits text nodes at markers into <citation n> elements; markers that ended up
 * inside code are dropped. */
export function remarkCitations() {
  return (tree: MdNode) => {
    walk(tree);
  };
}

function walk(node: MdNode): void {
  if (!node.children) return;
  const children: MdNode[] = [];
  for (const child of node.children) {
    if (child.type === "text" && child.value?.includes(OPEN)) {
      children.push(...splitText(child.value));
    } else if ((child.type === "inlineCode" || child.type === "code") && child.value?.includes(OPEN)) {
      children.push({ ...child, value: child.value.replace(MARKER, "") });
    } else {
      walk(child);
      children.push(child);
    }
  }
  node.children = children;
}

function splitText(value: string): MdNode[] {
  const nodes: MdNode[] = [];
  let index = 0;
  for (const match of value.matchAll(MARKER)) {
    if (match.index > index) nodes.push({ type: "text", value: value.slice(index, match.index) });
    nodes.push({
      type: "citationMarker",
      children: [],
      data: { hName: CITATION_TAG, hProperties: { n: match[1] } },
    });
    index = match.index + match[0].length;
  }
  if (index < value.length) nodes.push({ type: "text", value: value.slice(index) });
  return nodes;
}
