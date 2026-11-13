"use client";
import { Activity, BarChart3, Bot, Brain, Cable, Cpu, FolderKanban, Gauge, GitBranch, Globe, KanbanSquare, LayoutDashboard, ListTree, LogOut, Menu, Network, Plug, Puzzle, ScrollText, Settings, ShieldCheck, Users, Waypoints, Workflow, X, type LucideIcon } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { LanguageSwitcher } from "@/components/language-switcher";
import { AppProviders, useCan } from "@/components/providers";
import { ThemeToggle } from "@/components/theme-toggle";
import { Toaster } from "@/components/toaster";
import { Button } from "@/components/ui/button";
import { api, type User } from "@/lib/api";
import { useApprovals } from "@/lib/approvals";
import { useLiveUpdates } from "@/lib/live";
import { useShowAdvanced } from "@/lib/nav-mode";
import { cn } from "@/lib/utils";

// The main navigation (Phase 23) plus the AI Workforce (ADR-033), in three groups (ADR-036): daily work, setup, and the
// older general-purpose tools, which are hidden until "Show advanced tools" is on in Settings (they still power the
// Workforce and every address still opens). Items are hidden when the user lacks the permission; the backend enforces
// every permission regardless.
type NavKey = "dashboard" | "workforce" | "wfTasks" | "wfWorkers" | "projects" | "agents" | "teams" | "tasks" |
  "orchestration" | "supervisor" | "executions" | "bridge" | "integrations" | "providers" | "runtime" | "memory" | "skills" |
  "github" | "approvals" | "evaluations" | "analytics" | "audit" | "settings";
type NavItem = { href: string; key: NavKey; icon: LucideIcon; perm?: string };
type NavGroup = { key: "daily" | "setup" | "advanced"; items: NavItem[] };
export const NAV_GROUPS: NavGroup[] = [
  { key: "daily", items: [
    { href: "/workforce/room", key: "workforce", icon: Waypoints, perm: "workforce.read" },
    { href: "/workforce/tasks", key: "wfTasks", icon: KanbanSquare, perm: "workforce.read" },
    { href: "/workforce/workers", key: "wfWorkers", icon: Users, perm: "workforce.read" },
    { href: "/approvals", key: "approvals", icon: ShieldCheck, perm: "approvals.read" },
    { href: "/projects", key: "projects", icon: FolderKanban, perm: "projects.read" },
    { href: "/", key: "dashboard", icon: LayoutDashboard },
  ] },
  { key: "setup", items: [
    { href: "/providers", key: "providers", icon: Plug },
    { href: "/browser-bridge", key: "bridge", icon: Globe, perm: "providers.read" },
    { href: "/settings", key: "settings", icon: Settings },
  ] },
  { key: "advanced", items: [
    { href: "/agents", key: "agents", icon: Bot },
    { href: "/teams", key: "teams", icon: Users, perm: "teams.read" },
    { href: "/tasks", key: "tasks", icon: KanbanSquare },
    { href: "/orchestration", key: "orchestration", icon: Workflow, perm: "tasks.read" },
    { href: "/supervisor", key: "supervisor", icon: Activity, perm: "agents.read" },
    { href: "/executions", key: "executions", icon: ListTree, perm: "executions.read" },
    { href: "/integrations", key: "integrations", icon: Network, perm: "agents.read" },
    { href: "/runtime", key: "runtime", icon: Cpu, perm: "executions.read" },
    { href: "/memory", key: "memory", icon: Brain, perm: "memory.read" },
    { href: "/skills", key: "skills", icon: Puzzle, perm: "agents.read" },
    { href: "/github", key: "github", icon: GitBranch, perm: "github.read" },
    { href: "/evaluations", key: "evaluations", icon: Gauge, perm: "tasks.read" },
    { href: "/analytics", key: "analytics", icon: BarChart3, perm: "dashboard.read" },
    { href: "/audit", key: "audit", icon: ScrollText, perm: "audit.read" },
  ] },
];

const ALL = NAV_GROUPS.flatMap((g) => g.items);
const under = (path: string, href: string) => (href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`));

/** The item for the current page: the most specific match; any other AI Workforce page belongs to the Operations Room. */
function activeHref(path: string): string | undefined {
  const hit = ALL.filter((n) => under(path, n.href)).sort((a, b) => b.href.length - a.href.length)[0];
  if (hit) return hit.href;
  return path.startsWith("/workforce") ? "/workforce/room" : undefined;
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const t = useTranslations("nav");
  const pathname = usePathname();
  const can = useCan();
  const advanced = useShowAdvanced();
  const pending = useApprovals({ status: "pending" }, can("approvals.read")).data?.counts.pending ?? 0;
  const current = activeHref(pathname);
  return (
    <div className="flex flex-col gap-5">
      {NAV_GROUPS.map((g) => {
        const items = g.items.filter((n) => !n.perm || can(n.perm));
        // the advanced group stays hidden unless it is switched on, or the page open now is one of its pages
        if (g.key === "advanced" && !advanced && !items.some((n) => n.href === current)) return null;
        if (!items.length) return null;
        return (
          <section key={g.key} aria-labelledby={`nav-g-${g.key}`} data-testid={`nav-group-${g.key}`}>
            <h2 id={`nav-g-${g.key}`} className="mb-1 px-3 text-xs font-semibold text-muted-foreground">{t(`groups.${g.key}`)}</h2>
            <ul className="flex flex-col gap-1">
              {items.map(({ href, key, icon: Icon }) => {
                const active = href === current;
                return (
                  <li key={href}>
                    <Link
                      href={href}
                      onClick={onNavigate}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "flex min-h-10 items-center gap-3 rounded-md px-3 text-sm font-medium transition-colors duration-200",
                        active ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground",
                      )}
                    >
                      <Icon className="size-4 shrink-0" aria-hidden />
                      {t(key)}
                      {key === "approvals" && pending > 0 && (
                        <span className="ms-auto rounded-full bg-destructive px-2 py-0.5 text-xs font-semibold tabular-nums text-destructive-foreground" data-testid="nav-approvals-count">
                          <span aria-hidden>{pending}</span><span className="sr-only">{t("pendingApprovals", { n: pending })}</span>
                        </span>
                      )}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
      {!advanced && (
        <Link href="/settings#nav-advanced" onClick={onNavigate} className="px-3 text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground" data-testid="nav-show-advanced">
          {t("showAdvancedHint")}
        </Link>
      )}
    </div>
  );
}

function Brand() {
  const t = useTranslations("app");
  return (
    <Link href="/workforce/room" className="flex items-center gap-2 px-2 font-semibold">
      <span className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
        <Bot className="size-4" aria-hidden />
      </span>
      <span className="truncate">{t("name")}</span>
    </Link>
  );
}

export function AppShell({ user, children }: { user: User; children: React.ReactNode }) {
  const t = useTranslations();
  const locale = useLocale();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  async function logout() {
    await api("/api/auth/logout", { method: "POST" }).catch(() => undefined);
    window.location.assign("/login");
  }

  return (
    <AppProviders user={user}>
    <div className="flex min-h-dvh">
      {/* Desktop sidebar — on the start side (right in Arabic, left in English) */}
      <aside className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col gap-6 overflow-y-auto border-e bg-card p-4 lg:flex">
        <Brand />
        <nav aria-label={t("nav.label")}>
          <NavLinks />
        </nav>
      </aside>

      {/* Mobile drawer */}
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            className="absolute inset-0 bg-black/40"
            aria-label={t("nav.closeMenu")}
            onClick={() => setOpen(false)}
          />
          <aside
            role="dialog"
            aria-modal="true"
            aria-label={t("nav.label")}
            className="absolute inset-y-0 start-0 flex w-72 max-w-[85vw] flex-col gap-6 overflow-y-auto bg-card p-4 shadow-xl"
          >
            <div className="flex items-center justify-between">
              <Brand />
              <Button variant="ghost" size="icon" aria-label={t("nav.closeMenu")} onClick={() => setOpen(false)} autoFocus>
                <X aria-hidden />
              </Button>
            </div>
            <nav aria-label={t("nav.label")}>
              <NavLinks onNavigate={() => setOpen(false)} />
            </nav>
          </aside>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-16 items-center gap-2 border-b bg-background/90 px-4 backdrop-blur">
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            aria-label={t("nav.openMenu")}
            aria-expanded={open}
            onClick={() => setOpen(true)}
          >
            <Menu aria-hidden />
          </Button>
          <div className="flex-1" />
          <LiveIndicator />
          <ThemeToggle />
          <LanguageSwitcher signedIn />
          <div className="hidden flex-col items-end leading-tight sm:flex" data-testid="current-user">
            <span className="text-sm font-medium" dir="ltr">{user.username}</span>
            <span className="text-xs text-muted-foreground">{locale === "ar" ? user.role_name_ar : user.role_name_en}</span>
          </div>
          <Button variant="ghost" size="icon" aria-label={t("auth.logout")} title={t("auth.logout")} onClick={logout}>
            <LogOut className="rtl:-scale-x-100" aria-hidden />
          </Button>
        </header>
        <main id="main" className="flex-1 p-4 sm:p-6 lg:p-8">
          {children}
        </main>
      </div>
      <Toaster />
    </div>
    </AppProviders>
  );
}


/** UX-02: shows whether live updates are flowing; the views refresh themselves either way. */
function LiveIndicator() {
  const t = useTranslations("live");
  const state = useLiveUpdates();
  return (
    <span className="inline-flex items-center gap-1.5 px-2 text-xs text-muted-foreground" data-testid="live-state" data-state={state} title={t(state)}>
      <span aria-hidden className={cn("size-2 rounded-full", state === "live" ? "bg-success" : state === "offline" ? "bg-warning" : "bg-muted-foreground")} />
      <span className="sr-only sm:not-sr-only">{t(state)}</span>
    </span>
  );
}
