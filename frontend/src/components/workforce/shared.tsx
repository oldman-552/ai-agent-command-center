"use client";
import {
  Ban, Bot, CheckCircle2, CircleDashed, ClipboardList, Eye, FlaskConical, Hand, Hourglass, Inbox, Lock, type LucideIcon,
  PlayCircle, Power, RotateCcw, Send, ShieldCheck, Timer, UserCheck, XCircle,
} from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ApiError } from "@/lib/api";
import { useToasts } from "@/lib/toast";
import { cn } from "@/lib/utils";
import type { Lifecycle, Mode } from "@/lib/workforce";

const TABS = [
  { href: "/workforce/room", key: "room" },
  { href: "/workforce", key: "overview" },
  { href: "/workforce/tasks", key: "tasks" },
  { href: "/workforce/workers", key: "workers" },
  { href: "/workforce/bridge", key: "bridge" },
] as const;

/** Section navigation of the AI Workforce pages (the same order on every page). */
export function WorkforceTabs() {
  const t = useTranslations("workforce.tabs");
  const pathname = usePathname();
  return (
    <nav aria-label={t("label")} className="flex flex-wrap gap-1 border-b" data-testid="wf-tabs">
      {TABS.map(({ href, key }) => {
        const active = href === "/workforce" ? pathname === href || pathname.startsWith("/workforce/objectives") : pathname.startsWith(href);
        return (
          <Link key={href} href={href} aria-current={active ? "page" : undefined}
            className={cn("-mb-px flex min-h-11 items-center border-b-2 px-4 text-sm font-medium transition-colors duration-200 focus-visible:outline-2 focus-visible:outline-ring",
              active ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground")}>
            {t(key)}
          </Link>
        );
      })}
    </nav>
  );
}

export function PageHeader({ title, subtitle, children }: { title: string; subtitle?: string; children?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-2xl font-bold">{title}</h1>
        {subtitle && <p className="max-w-3xl text-sm text-muted-foreground">{subtitle}</p>}
      </div>
      {children && <div className="flex flex-wrap gap-2">{children}</div>}
    </div>
  );
}

const LIFECYCLE: Record<Lifecycle, { icon: LucideIcon; tone: string }> = {
  DRAFT: { icon: CircleDashed, tone: "text-muted-foreground" },
  PLANNED: { icon: ClipboardList, tone: "text-muted-foreground" },
  READY: { icon: Timer, tone: "text-primary" },
  ASSIGNED: { icon: Send, tone: "text-primary" },
  WAITING_FOR_ACCEPTANCE: { icon: Inbox, tone: "text-warning" },
  ACCEPTED: { icon: UserCheck, tone: "text-primary" },
  QUEUED: { icon: Hourglass, tone: "text-primary" },
  RUNNING: { icon: PlayCircle, tone: "text-primary" },
  SUBMITTED: { icon: Eye, tone: "text-warning" },
  UNDER_REVIEW: { icon: Eye, tone: "text-warning" },
  APPROVED: { icon: ShieldCheck, tone: "text-warning" },
  REJECTED: { icon: XCircle, tone: "text-destructive" },
  REVISION_REQUESTED: { icon: RotateCcw, tone: "text-warning" },
  COMPLETED: { icon: CheckCircle2, tone: "text-success" },
  FAILED: { icon: XCircle, tone: "text-destructive" },
  BLOCKED: { icon: Lock, tone: "text-warning" },
  CANCELLED: { icon: Ban, tone: "text-muted-foreground" },
};

const CHIP = "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium text-foreground";

/** Full-contrast text on a neutral chip; the state colour tints only the icon (never colour alone). */
export function LifecycleBadge({ state, className }: { state: Lifecycle; className?: string }) {
  const t = useTranslations("workforce.lifecycle");
  const m = LIFECYCLE[state] ?? LIFECYCLE.DRAFT;
  return (
    <span className={cn(CHIP, className)} data-testid="wf-lifecycle" data-state={state}>
      <m.icon className={cn("size-3.5", m.tone)} aria-hidden />{t.has(state) ? t(state) : state}
    </span>
  );
}

const MODE: Record<Mode, { icon: LucideIcon; tone: string }> = {
  AUTOMATIC: { icon: Bot, tone: "text-primary" },
  HUMAN_ASSISTED: { icon: Hand, tone: "text-success" },
  DISABLED: { icon: Power, tone: "text-muted-foreground" },
};

export function ModeBadge({ mode, className }: { mode: Mode; className?: string }) {
  const t = useTranslations("workforce.mode");
  const m = MODE[mode] ?? MODE.DISABLED;
  return (
    <span className={cn(CHIP, className)} data-testid="wf-mode" data-mode={mode}>
      <m.icon className={cn("size-3.5", m.tone)} aria-hidden />{t(mode)}
    </span>
  );
}

const OBJECTIVE: Record<string, { icon: LucideIcon; tone: string }> = {
  planning: { icon: Hourglass, tone: "text-primary" },
  running: { icon: PlayCircle, tone: "text-primary" },
  completed: { icon: CheckCircle2, tone: "text-success" },
  failed: { icon: XCircle, tone: "text-destructive" },
  cancelled: { icon: Ban, tone: "text-muted-foreground" },
};

export function ObjectiveStatus({ status }: { status: string }) {
  const t = useTranslations("workforce.objectiveStatus");
  const m = OBJECTIVE[status] ?? OBJECTIVE.planning;
  return (
    <span className={CHIP} data-testid="wf-objective-status" data-status={status}>
      <m.icon className={cn("size-3.5", m.tone)} aria-hidden />{t.has(status) ? t(status) : status}
    </span>
  );
}

/** Anything that passed through the mock provider or the fake bridge says so, everywhere. */
export function MockBadge({ short, className }: { short?: boolean; className?: string }) {
  const t = useTranslations("workforce");
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full border border-warning px-2 py-0.5 text-xs font-semibold", className)} data-testid="wf-mock">
      <FlaskConical className="size-3.5 text-warning" aria-hidden />{short ? t("mockShort") : t("mock")}
    </span>
  );
}

/** A masked address plus a short tag from its salted hash: two accounts that mask to the same text stay apart. */
export function accountTag(account: string, hash?: string | null): string {
  return hash ? `${account} #${hash.slice(0, 4)}` : account;
}

/** A machine reason code in words: `no_eligible_worker:worker_disabled` → "No worker can take it (worker disabled)". */
export function useReason() {
  const t = useTranslations("workforce.problems");
  return (code: string | null | undefined): string => {
    if (!code) return "";
    const [head, ...rest] = code.split(":");
    const tail = rest.join(":");
    const first = t.has(head) ? t(head) : head;
    if (!tail) return first;
    return `${first} (${t.has(tail) ? t(tail) : tail})`;
  };
}

/** Toast for a failed request: a translated sentence when the code is known, the code itself otherwise. */
export function useFail() {
  const t = useTranslations("workforce.errors");
  const tc = useTranslations("common");
  const toast = useToasts((s) => s.push);
  return (e: unknown) => {
    const code = e instanceof ApiError ? e.code : "network";
    const head = code.split(":")[0];
    toast("error", t.has(head) ? t(head) : tc("requestFailed", { code }));
  };
}

/** Actor strings of the platform (`user:3`, `manager`, `agent:7`, `system`) in words. */
export function useActor() {
  const t = useTranslations("workforce.actor");
  return (actor: string | null | undefined): string => {
    if (!actor) return t("system");
    if (actor.startsWith("user:")) return t("user", { id: actor.slice(5) });
    if (actor.startsWith("agent:")) return t("agent", { id: actor.slice(6) });
    return t.has(actor) ? t(actor) : actor;
  };
}
