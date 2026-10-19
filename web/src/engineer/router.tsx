import { createRootRoute, createRoute, createRouter, redirect, type RouterHistory } from "@tanstack/react-router";

import { Layout } from "./Layout";
import { ComparePage } from "./pages/compare/ComparePage";
import { DagDetailPage } from "./pages/dags/DagDetailPage";
import { DagsPage } from "./pages/dags/DagsPage";
import { DocumentPage } from "./pages/docs/DocumentPage";
import { RunDetailPage } from "./pages/runs/RunDetailPage";
import { RunsPage } from "./pages/runs/RunsPage";
import { SearchPage } from "./pages/search/SearchPage";
import { SourcesPage } from "./pages/sources/SourcesPage";
import { VersionPage } from "./pages/versions/VersionPage";
import {
  validateCompareSearch,
  validateDagSearch,
  validateDocumentSearch,
  validateRunSearch,
  validateRunsSearch,
  validateSearchSearch,
  validateSourcesSearch,
  validateVersionSearch,
} from "./search";

const rootRoute = createRootRoute({ component: Layout });

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/dags" });
  },
});

const dagsRoute = createRoute({ getParentRoute: () => rootRoute, path: "/dags", component: DagsPage });

const dagRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/dags/$dag",
  validateSearch: validateDagSearch,
  component: DagDetailPage,
});

const documentRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/dags/$dag/docs/$docId",
  validateSearch: validateDocumentSearch,
  component: DocumentPage,
});

const runsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/runs",
  validateSearch: validateRunsSearch,
  component: RunsPage,
});

const runRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/runs/$runId",
  validateSearch: validateRunSearch,
  component: RunDetailPage,
});

const versionRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/versions/$versionId",
  validateSearch: validateVersionSearch,
  component: VersionPage,
});

const compareRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/compare",
  validateSearch: validateCompareSearch,
  component: ComparePage,
});

const searchRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/search",
  validateSearch: validateSearchSearch,
  component: SearchPage,
});

const sourcesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/sources",
  validateSearch: validateSourcesSearch,
  component: SourcesPage,
});

export const routeTree = rootRoute.addChildren([
  indexRoute,
  dagsRoute,
  dagRoute,
  documentRoute,
  runsRoute,
  runRoute,
  versionRoute,
  compareRoute,
  searchRoute,
  sourcesRoute,
]);

export interface AppRouterOptions {
  history?: RouterHistory;
  InnerWrap?: (props: { children: React.ReactNode }) => React.JSX.Element;
}

export function createAppRouter(options: AppRouterOptions = {}) {
  return createRouter({ routeTree, defaultPreload: "intent", scrollRestoration: true, ...options });
}

declare module "@tanstack/react-router" {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}
