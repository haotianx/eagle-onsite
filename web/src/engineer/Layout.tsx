import { Link, Outlet } from "@tanstack/react-router";

import { useRepoEvents } from "@/api/sse";
import { Toaster } from "@/components/ui/sonner";

const NAV = [
  { to: "/dags", label: "DAGs" },
  { to: "/runs", label: "Runs" },
  { to: "/search", label: "Search" },
  { to: "/sources", label: "Sources" },
] as const;

export function Layout() {
  useRepoEvents();
  return (
    <div className="flex h-svh flex-col">
      <header className="flex h-11 shrink-0 items-center gap-6 border-b px-4">
        <span className="text-sm font-semibold">Ingest</span>
        <nav className="flex items-center gap-1 text-sm">
          {NAV.map(({ to, label }) => (
            <Link
              key={to}
              to={to}
              className="rounded-md px-2.5 py-1 text-muted-foreground hover:text-foreground"
              activeProps={{ className: "bg-muted text-foreground" }}
            >
              {label}
            </Link>
          ))}
        </nav>
      </header>
      <main className="min-h-0 flex-1 overflow-auto">
        <Outlet />
      </main>
      <Toaster position="bottom-right" />
    </div>
  );
}
