import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { SearchIn } from "@/api/types";
import { json, mockFetch, renderWithProviders } from "@/test/utils";

import { hit, node, version } from "./fixtures";
import { SearchPage } from "./SearchPage";

const versions = [version(3, { scratch: true }), version(2), version(1, { current: true })];

function mockApi(search: unknown = defaultSearch) {
  const bodies: SearchIn[] = [];
  const fetchMock = mockFetch({
    "GET /api/dags": [{ name: "default" }],
    "GET /api/dags/:dag": {
      name: "default",
      nodes: [node("chunk"), node("embed", { current: versions[2], candidates: [versions[1]] })],
    },
    "GET /api/dags/:dag/nodes/:node/versions": versions,
    "POST /api/search": async (request: Request) => {
      const body = (await request.json()) as SearchIn;
      bodies.push(body);
      return typeof search === "function" ? search(body) : search;
    },
  });
  return { bodies, fetchMock };
}

function defaultSearch(body: SearchIn) {
  const side = (id: number, hits: ReturnType<typeof hit>[]) => ({
    version: versions.find((v) => v.id === id),
    model: "Qwen/Qwen3-Embedding-0.6B",
    hits,
  });
  return {
    sides: [
      side(body.versions[0], [hit(1, { text_hash: "a" }), hit(2, { text_hash: "b" })]),
      side(body.versions[1], [
        hit(1, { text_hash: "b" }),
        hit(2, { text_hash: "n", chunk_id: "new", doc_id: "d9", pages: [7] }),
      ]),
    ],
  };
}

describe("SearchPage", () => {
  it("defaults to current vs newest candidate and syncs a submitted search to the URL", async () => {
    const { bodies } = mockApi();
    const { router } = await renderWithProviders(<SearchPage />, { path: "/search" });

    expect(await screen.findByRole("combobox", { name: "Left version" })).toHaveTextContent("default/embed@v1");
    expect(screen.getByRole("combobox", { name: "Right version" })).toHaveTextContent("default/embed@v2");

    await userEvent.type(screen.getByRole("textbox", { name: "Query" }), "pump schedule{Enter}");
    expect(router.state.location.search).toEqual({ q: "pump schedule", a: 1, b: 2 });
    await screen.findAllByText("Qwen/Qwen3-Embedding-0.6B");
    expect(bodies).toEqual([{ query: "pump schedule", versions: [1, 2], k: 10 }]);
  });

  it("shows rank changes on the right and links hits to the PDF page", async () => {
    mockApi();
    await renderWithProviders(<SearchPage />, { path: "/search?q=pumps&a=1&b=2&k=20" });

    const [left, right] = await screen.findAllByRole("list");
    expect(within(right).getByText("was 2")).toBeInTheDocument();
    expect(within(right).getByText("new")).toBeInTheDocument();
    expect(within(left).queryByText(/was|new/)).toBeNull();

    const links = within(right).getAllByRole("link");
    expect(links[1]).toHaveAttribute("href", "/dags/default/docs/d9?node=embed&version=2&page=7");
    expect(screen.getByRole("combobox", { name: "Top k" })).toHaveTextContent("Top 20");
  });

  it("runs the search from the URL and re-runs on version and k changes", async () => {
    const { bodies } = mockApi();
    const { router } = await renderWithProviders(<SearchPage />, { path: "/search?q=pumps&a=1&b=3" });
    await screen.findAllByRole("list");
    expect(bodies.at(-1)).toEqual({ query: "pumps", versions: [1, 3], k: 10 });

    await userEvent.click(screen.getByRole("combobox", { name: "Top k" }));
    await userEvent.click(await screen.findByRole("option", { name: "Top 5" }));
    expect(router.state.location.search).toEqual({ q: "pumps", a: 1, b: 3, k: 5 });
    await act(() => router.navigate({ to: "/search", search: { q: "valves", a: 2, b: 1, k: 5 } }));
    expect(screen.getByRole("textbox", { name: "Query" })).toHaveValue("valves");
    await screen.findAllByRole("list");
    expect(bodies.slice(1)).toEqual([
      { query: "pumps", versions: [1, 3], k: 5 },
      { query: "valves", versions: [2, 1], k: 5 },
    ]);
  });

  it("shows the API's error detail", async () => {
    mockApi(() => json({ detail: "default/embed@v3 has no vectors" }, 400));
    await renderWithProviders(<SearchPage />, { path: "/search?q=pumps&a=1&b=3" });
    expect(await screen.findByText("default/embed@v3 has no vectors")).toBeInTheDocument();
  });
});
