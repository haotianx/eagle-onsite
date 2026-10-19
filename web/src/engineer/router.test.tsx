import { getRouteApi } from "@tanstack/react-router";
import { act, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { FakeEventSource, mockFetch, renderRoute, renderWithProviders } from "@/test/utils";

describe("engineer routes", () => {
  it("redirects / to /dags and renders the page in the layout", async () => {
    const { router } = await renderRoute("/");
    expect(router.state.location.pathname).toBe("/dags");
    expect(await screen.findByRole("link", { name: "DAGs" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Runs" })).toHaveAttribute("href", "/runs");
  });

  it("matches a page by path", async () => {
    const { router } = await renderRoute("/versions/12?tab=issues");
    const match = router.state.matches.at(-1);
    expect(match?.routeId).toBe("/versions/$versionId");
    expect(match?.params).toEqual({ versionId: "12" });
    expect(match?.search).toEqual({ tab: "issues" });
  });

  it("validates search params", async () => {
    const { router } = await renderRoute("/versions/12?tab=bogus&level=error");
    expect(router.state.matches.at(-1)?.search).toEqual({ level: "error" });
  });

  it("gives components the matched route's params and search", async () => {
    const route = getRouteApi("/dags/$dag/docs/$docId");
    function Probe() {
      const { dag, docId } = route.useParams();
      const { page } = route.useSearch();
      return (
        <p>
          {dag}/{docId}/{page}
        </p>
      );
    }
    const { router } = await renderWithProviders(<Probe />, { path: "/dags/default/docs/abc?page=3" });
    expect(screen.getByText("default/abc/3")).toBeInTheDocument();
    await act(() => router.navigate({ to: "/dags/$dag/docs/$docId", params: { dag: "default", docId: "abc" }, search: { page: 4 } }));
    expect(screen.getByText("default/abc/4")).toBeInTheDocument();
  });

  it("refreshes DAG queries on repo_changed", async () => {
    let calls = 0;
    mockFetch({ "GET /api/dags": () => (calls++, []) });
    const { queryClient } = await renderRoute("/dags");
    await queryClient.fetchQuery({ queryKey: ["dags"], queryFn: () => fetch("/api/dags").then((r) => r.json()) });
    expect(calls).toBe(1);
    const source = FakeEventSource.last("/api/events")!;
    expect(source).toBeDefined();
    act(() => source.emit("repo_changed", { paths: ["dags/default.yaml"] }));
    expect(queryClient.getQueryState(["dags"])?.isInvalidated).toBe(true);
  });
});
