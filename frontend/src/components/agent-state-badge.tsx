"use client";
import { AlertTriangle, Ban, CheckCircle2, CircleDot, Clock, Hourglass, Loader2, type LucideIcon, PauseCircle, PlayCircle, ShieldAlert, Sparkles, XCircle } from "lucide-react";
import { useTranslations } from "next-intl";
import { cn } from "@/lib/utils";

const MAP: Record<string, { icon: LucideIcon; tone: string }> = {
  CREATED: { icon: Sparkles, tone: "text-muted-foreground" },
  INITIALIZING: { icon: Loader2, tone: "text-muted-foreground" },
  IDLE: { icon: CircleDot, tone: "text-success" },
  ASSIGNED: { icon: Clock, tone: "text-primary" },
  RUNNING: { icon: PlayCircle, tone: "text-primary" },
  REVIEWING: { icon: Hourglass, tone: "text-primary" },
  COMPLETED: { icon: CheckCircle2, tone: "text-success" },
  WAITING_FOR_INPUT: { icon: PauseCircle, tone: "text-warning" },
  WAITING_FOR_APPROVAL: { icon: ShieldAlert, tone: "text-warning" },
  BLOCKED: { icon: AlertTriangle, tone: "text-warning" },
  FAILED: { icon: XCircle, tone: "text-destructive" },
  CANCELLED: { icon: Ban, tone: "text-muted-foreground" },
  DISABLED: { icon: Ban, tone: "text-muted-foreground" },
};

/**
 * Agent state: full-contrast text on a neutral chip; the state colour tints only the icon (never colour alone).
 * Tinted text on a tinted chip measured 4.38:1 for the green and amber families — below the 4.5:1 that small text needs.
 */
export function AgentStateBadge({ state }: { state: string }) {
  const t = useTranslations("agentStates");
  const m = MAP[state] ?? MAP.CREATED;
  const Icon = m.icon;
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium text-foreground">
      <Icon className={cn("size-3.5", m.tone)} aria-hidden />
      {t.has(state) ? t(state) : state}
    </span>
  );
}
