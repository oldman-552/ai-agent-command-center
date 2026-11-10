"use client";
import { Ban, CheckCircle2, CircleDashed, ClipboardList, Eye, type LucideIcon, Lock, PlayCircle, ShieldCheck, Timer, XCircle } from "lucide-react";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";

const MAP: Record<string, { icon: LucideIcon; tone: string }> = {
  BACKLOG: { icon: CircleDashed, tone: "bg-muted text-muted-foreground" },
  PLANNED: { icon: ClipboardList, tone: "bg-muted text-muted-foreground" },
  READY: { icon: Timer, tone: "bg-primary/10 text-primary" },
  IN_PROGRESS: { icon: PlayCircle, tone: "bg-primary/10 text-primary" },
  BLOCKED: { icon: Lock, tone: "bg-warning/10 text-warning" },
  REVIEW: { icon: Eye, tone: "bg-warning/10 text-warning" },
  APPROVAL: { icon: ShieldCheck, tone: "bg-warning/10 text-warning" },
  COMPLETED: { icon: CheckCircle2, tone: "bg-success/10 text-success" },
  FAILED: { icon: XCircle, tone: "bg-destructive/10 text-destructive" },
  CANCELLED: { icon: Ban, tone: "bg-muted text-muted-foreground" },
};

/** Task status: full-contrast text on a neutral chip; the status color tints only the icon (never color alone). */
export function TaskStatusBadge({ status, className }: { status: string; className?: string }) {
  const t = useTranslations("taskStatus");
  const m = MAP[status] ?? MAP.BACKLOG;
  const Icon = m.icon;
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium text-foreground", className)}>
      <Icon className={cn("size-3.5", statusTone(status))} aria-hidden />
      {t.has(status) ? t(status) : status}
    </span>
  );
}

export function statusIcon(status: string): LucideIcon {
  return (MAP[status] ?? MAP.BACKLOG).icon;
}

/** Text color of the status family, for icons on neutral backgrounds. */
export function statusTone(status: string): string {
  return (MAP[status] ?? MAP.BACKLOG).tone.split(" ").find((c) => c.startsWith("text-")) ?? "";
}
