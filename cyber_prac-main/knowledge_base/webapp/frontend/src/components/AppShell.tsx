import { useEffect } from "react";
import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import {
  LayoutDashboard,
  FolderTree,
  ShieldCheck,
  Search,
  Command as CommandIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useUi } from "@/stores/ui";
import { CommandPalette } from "@/components/CommandPalette";
import { Toaster } from "@/components/ui";

const NAV = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/explorer", label: "Explorer", icon: FolderTree },
  { to: "/validation", label: "Validation", icon: ShieldCheck },
] as const;

export function AppShell() {
  const setCommandOpen = useUi((s) => s.setCommandOpen);
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCommandOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setCommandOpen]);

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Sidebar */}
      <aside className="flex w-56 shrink-0 flex-col border-r border-border-subtle bg-bg-raised">
        <div className="flex h-12 items-center gap-2 px-4">
          <div className="flex h-6 w-6 items-center justify-center rounded bg-accent text-xs font-bold text-white">
            C
          </div>
          <span className="text-sm font-semibold tracking-tight">CyberAI</span>
          <span className="text-2xs text-fg-faint">FMS</span>
        </div>

        <button
          onClick={() => setCommandOpen(true)}
          className="mx-3 mb-2 flex items-center gap-2 rounded-md border border-border-subtle bg-bg-base px-2.5 py-1.5 text-xs text-fg-faint transition-colors hover:border-border-strong hover:text-fg-muted"
        >
          <Search className="h-3.5 w-3.5" />
          <span>Search…</span>
          <kbd className="ml-auto flex items-center gap-0.5 text-2xs">
            <CommandIcon className="h-2.5 w-2.5" />K
          </kbd>
        </button>

        <nav className="flex flex-col gap-0.5 px-3 py-1">
          {NAV.map((item) => {
            const active =
              item.to === "/" ? pathname === "/" : pathname.startsWith(item.to);
            return (
              <Link
                key={item.to}
                to={item.to}
                className={cn(
                  "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm transition-colors",
                  active
                    ? "bg-bg-hover text-fg"
                    : "text-fg-muted hover:bg-bg-hover hover:text-fg",
                )}
              >
                <item.icon className="h-4 w-4" />
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="mt-auto p-3 text-2xs text-fg-faint">
          <div className="rounded-md border border-border-subtle bg-bg-base px-2.5 py-2">
            <div className="font-medium text-fg-muted">admin@cyberai</div>
            <div>Administrator · edit access</div>
          </div>
        </div>
      </aside>

      {/* Main */}
      <main className="flex min-w-0 flex-1 flex-col">
        <Outlet />
      </main>

      <CommandPalette />
      <Toaster />
    </div>
  );
}
