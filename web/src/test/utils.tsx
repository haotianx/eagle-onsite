import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, RouterProvider } from "@tanstack/react-router";
import { render, type RenderResult } from "@testing-library/react";
import type { ReactNode } from "react";
import { vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { createAppRouter } from "@/engineer/router";

export { FakeEventSource } from "./fake-event-source";

export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } },
  });
}

type AppRouter = ReturnType<typeof createAppRouter>;

export interface RenderWithProvidersResult extends RenderResult {
  router: AppRouter;
  queryClient: QueryClient;
}

/** Renders `ui` in place of the page matched at `path` (default "/dags"), so components using
 * getRouteApi for that route see its params and search. Navigation updates the router state. */
export async function renderWithProviders(
  ui: ReactNode,
  { path = "/dags", queryClient = createTestQueryClient() }: { path?: string; queryClient?: QueryClient } = {},
): Promise<RenderWithProvidersResult> {
  const router = createAppRouter({
    history: createMemoryHistory({ initialEntries: [path] }),
    InnerWrap: () => <>{ui}</>,
  });
  // Without the route tree mounted nothing else loads the router on navigation.
  router.history.subscribe(() => void router.load());
  await router.load();
  return { ...render(<Providers router={router} queryClient={queryClient} />), router, queryClient };
}

/** Renders the whole engineer app (layout and page) at `path`. */
export async function renderRoute(
  path: string,
  { queryClient = createTestQueryClient() }: { queryClient?: QueryClient } = {},
): Promise<RenderWithProvidersResult> {
  const router = createAppRouter({ history: createMemoryHistory({ initialEntries: [path] }) });
  await router.load();
  return { ...render(<Providers router={router} queryClient={queryClient} />), router, queryClient };
}

function Providers({ router, queryClient }: { router: AppRouter; queryClient: QueryClient }) {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <RouterProvider router={router} />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

type Handler = unknown | ((request: Request, params: Record<string, string>) => unknown);

/** JSON response helper for mockFetch handlers. */
export function json(body: unknown, status = 200): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** Stubs fetch. Keys are "METHOD /api/path" with `:name` path params (query strings are ignored);
 * values are a response body, a Response, or a function returning either. Unmatched requests get
 * a 404 naming the request. Returns the mock, whose calls hold the Requests. */
export function mockFetch(routes: Record<string, Handler>) {
  const compiled = Object.entries(routes).map(([key, handler]) => {
    const [method, pattern] = key.split(" ", 2);
    const names: string[] = [];
    const source = pattern
      .split("/")
      .map((part) => {
        if (part.startsWith(":")) {
          names.push(part.slice(1));
          return "([^/]+)";
        }
        return part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      })
      .join("/");
    return { method: method.toUpperCase(), regex: new RegExp(`^${source}$`), names, handler };
  });

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), "http://localhost"), init);
    const url = new URL(request.url);
    for (const route of compiled) {
      const match = route.method === request.method ? route.regex.exec(url.pathname) : null;
      if (!match) continue;
      const params = Object.fromEntries(route.names.map((name, i) => [name, decodeURIComponent(match[i + 1])]));
      const result =
        typeof route.handler === "function"
          ? await (route.handler as (r: Request, p: Record<string, string>) => unknown)(request, params)
          : route.handler;
      return result instanceof Response ? result : json(result);
    }
    return json({ detail: `Unmocked request: ${request.method} ${url.pathname}` }, 404);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
