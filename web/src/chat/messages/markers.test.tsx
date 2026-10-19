import { render } from "@testing-library/react";
import { defaultRemarkPlugins, Streamdown } from "streamdown";

import { answerMarkdown, answerText, CITATION_TAG, marker, remarkCitations } from "./markers";

test("markers follow the cited text, before trailing whitespace and inside a table's last cell", () => {
  expect(
    answerMarkdown([
      { type: "text", text: "Fee $5.\n\n" },
      { type: "citation", n: 1 },
      { type: "citation", n: 1 },
      { type: "citation", n: 2 },
      { type: "text", text: "| a | b |\n|---|---|\n| x | y |\n" },
      { type: "citation", n: 3 },
    ]),
  ).toBe(`Fee $5.${marker(1)}${marker(2)}\n\n| a | b |\n|---|---|\n| x | y${marker(3)} |\n`);
  expect(answerText([{ type: "text", text: "Fee" }, { type: "citation", n: 1 }, { type: "text", text: " $5." }])).toBe(
    "Fee $5.",
  );
});

test("markers render as citation components through markdown, and are dropped from code", async () => {
  const markdown = answerMarkdown([
    { type: "text", text: "Fees are **$1,750.00**" },
    { type: "citation", n: 1 },
    { type: "text", text: "\n\n- item `x" },
    { type: "citation", n: 2 },
    { type: "text", text: "`\n- other" },
    { type: "citation", n: 3 },
    { type: "text", text: "\n\n| a | b |\n|---|---|\n| x | y |" },
    { type: "citation", n: 4 },
  ]);
  const { container, findAllByTestId } = render(
    <Streamdown
      remarkPlugins={[...Object.values(defaultRemarkPlugins), remarkCitations]}
      allowedTags={{ [CITATION_TAG]: ["n"] }}
      components={{ [CITATION_TAG]: (props: Record<string, unknown>) => <b data-testid="cite">[{String(props.n)}]</b> }}
    >
      {markdown}
    </Streamdown>,
  );
  expect((await findAllByTestId("cite")).map((el) => el.textContent)).toEqual(["[1]", "[3]", "[4]"]);
  expect(container.querySelector("p")!.textContent).toBe("Fees are $1,750.00[1]");
  expect(container.querySelector("li code")!.textContent).toBe("x");
  expect(container.querySelector("td:last-child")!.textContent).toBe("y[4]");
});
