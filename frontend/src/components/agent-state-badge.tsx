"use client";
import { AlertTriangle, Ban, CheckCircle2, CircleDot, Clock, Hourglass, Loader2, type LucideIcon, PauseCircle, PlayCircle, ShieldAlert, Sparkles, XCircle } from "lucide-react";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";

const MAP: Record<string, { icon: LucideIcon; tone: string }> = {
  CREATED: { icon: Sparkles, tone: "bg-muted text-muted-foreground" },
  INITIALIZING: { icon: Loader2, tone: "bg-muted text-muted-foreground" },
  IDLE: { icon: CircleDot, tone: "bg-success/10 text-success" },
  ASSIGNED: { icon: Clock, tone: "bg-primary/10 text-primary" },
  RUNNING: { icon: PlayCircle, tone: "bg-primary/10 text-primary" },
  REVIEWING: { icon: Hourglass, tone: "bg-primary/10 text-primary" },
  COMPLETED: { icon: CheckCircle2, tone: "bg-success/10 text-success" },
  WAITING_FOR_INPUT: { icon: PauseCircle, tone: "bg-warning/10 text-warning" },
  WAITING_FOR_APPROVAL: { icon: ShieldAlert, tone: "bg-warning/10 text-warning" },
  BLOCKED: { icon: AlertTriangle, tone: "bg-warning/10 text-warning" },
  FAILED: { icon: XCircle, tone: "bg-destructive/10 text-destructive" },
  CANCELLED: { icon: Ban, tone: "bg-muted text-muted-foreground" },
  DISABLED: { icon: Ban, tone: "bg-muted text-muted-foreground" },
};

/** Status shown with icon + text (never color alone). */
export function AgentStateBadge({ state }: { state: string }) {
  const t = useTranslations("agentStates");
  const m = MAP[state] ?? MAP.CREATED;
  const Icon = m.icon;
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium", m.tone)}>
      <Icon className="size-3.5" aria-hidden />
      {t.has(state) ? t(state) : state}
    </span>
  );
}
