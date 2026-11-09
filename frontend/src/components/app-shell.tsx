"use client";
import { Bot, LayoutDashboard, LogOut, Menu, X, type LucideIcon } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { LanguageSwitcher } from "@/components/language-switcher";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { api, type User } from "@/lib/api";
import { cn } from "@/lib/utils";

// Only pages that actually exist are listed. New pages are added here as they are built.
const NAV: { href: string; key: "dashboard"; icon: LucideIcon }[] = [
  { href: "/", key: "dashboard", icon: LayoutDashboard },
];

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const t = useTranslations("nav");
  const pathname = usePathname();
  return (
    <ul className="flex flex-col gap-1">
      {NAV.map(({ href, key, icon: Icon }) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
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
            </Link>
          </li>
        );
      })}
    </ul>
  );
}

function Brand() {
  const t = useTranslations("app");
  return (
    <Link href="/" className="flex items-center gap-2 px-2 font-semibold">
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
    <div className="flex min-h-dvh">
      {/* Desktop sidebar — on the start side (right in Arabic, left in English) */}
      <aside className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col gap-6 border-e bg-card p-4 lg:flex">
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
            className="absolute inset-y-0 start-0 flex w-72 max-w-[85vw] flex-col gap-6 bg-card p-4 shadow-xl"
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
    </div>
  );
}
