"use client";
import {
  Activity,
  AlertTriangle,
  Bot,
  CheckCircle2,
  CircleDollarSign,
  CirclePause,
  CircleX,
  Clock,
  FolderGit2,
  GitBranch,
  Hourglass,
  type LucideIcon,
  PlayCircle,
  RefreshCw,
  ShieldCheck,
  Sigma,
  Zap,
} from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

export type Summary = {
  agents: { total: number; active: number; available: number; disabled: number; failed: number };
  tasks: Record<string, number>;
  tasks_awaiting_approval: number;
  task_success_rate: number | null;
  projects: number;
  repositories: number;
  tokens_total: number;
  cost_usd_total: number;
  usage_series: { date: string; tokens: number; cost_usd: number }[];
  recent_activity: { id: number; ts: string; action: string; category: string; level: string; message: string }[];
};

const POLL_MS = 15_000;

function Stat({ label, value, icon: Icon, tone = "default", hint }: {
  label: string; value: string; icon: LucideIcon; tone?: "default" | "success" | "warning" | "danger"; hint?: string;
}) {
  const toneCls = {
    default: "bg-primary/10 text-primary",
    success: "bg-success/10 text-success",
    warning: "bg-warning/10 text-warning",
    danger: "bg-destructive/10 text-destructive",
  }[tone];
  return (
    <Card className="p-3 sm:p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm text-muted-foreground">{label}</p>
          <p className="mt-1 text-xl font-semibold tabular-nums sm:text-2xl"><bdi>{value}</bdi></p>
          {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
        </div>
        <span className={cn("flex size-9 shrink-0 items-center justify-center rounded-lg", toneCls)}>
          <Icon className="size-4" aria-hidden />
        </span>
      </div>
    </Card>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-sm font-semibold text-muted-foreground">{title}</h2>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">{children}</div>
    </section>
  );
}

export function DashboardView() {
  const t = useTranslations("dashboard");
  const ta = useTranslations("activity");
  const locale = useLocale();
  const numLocale = locale === "ar" ? "ar-u-nu-latn" : "en";
  const n = (v: number) => new Intl.NumberFormat(numLocale).format(v);
  const usd = (v: number) =>
    // USD is always shown as "$1,234.56" (Latin form) so it renders the same in RTL and LTR.
    new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(v);

  const [data, setData] = useState<Summary | null>(null);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await api<Summary>("/api/dashboard/summary"));
      setError(false);
      setUpdatedAt(new Date());
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  const hasUsage = data?.usage_series.some((d) => d.tokens > 0) ?? false;
  const actionLabel = (a: string) => (ta.has(`actions.${a}`) ? ta(`actions.${a}`) : a);

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{t("title")}</h1>
          <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
        </div>
        <div className="flex items-center gap-3">
          {updatedAt && (
            <span className="text-xs text-muted-foreground" aria-live="polite">
              {t("lastUpdated", { time: updatedAt.toLocaleTimeString(numLocale) })}
            </span>
          )}
          <Button variant="outline" size="sm" onClick={load} disabled={loading}>
            <RefreshCw className={cn(loading && "animate-spin")} aria-hidden />
            {t("refresh")}
          </Button>
        </div>
      </div>

      {error && !data && (
        <Card role="alert" className="flex flex-col items-center gap-3 p-8 text-center">
          <AlertTriangle className="size-6 text-destructive" aria-hidden />
          <p>{t("loadError")}</p>
          <Button variant="outline" onClick={load}>{t("retry")}</Button>
        </Card>
      )}

      {!data && !error && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5" aria-busy="true" aria-label={t("loading")}>
          {Array.from({ length: 10 }).map((_, i) => <Skeleton key={i} className="h-24" />)}
        </div>
      )}

      {data && (
        <>
          <Section title={t("sections.agents")}>
            <Stat label={t("agents.total")} value={n(data.agents.total)} icon={Bot} />
            <Stat label={t("agents.active")} value={n(data.agents.active)} icon={Zap} tone="success" />
            <Stat label={t("agents.available")} value={n(data.agents.available)} icon={CheckCircle2} />
            <Stat label={t("agents.disabled")} value={n(data.agents.disabled)} icon={CirclePause} tone="warning" />
            <Stat label={t("agents.failed")} value={n(data.agents.failed)} icon={CircleX} tone="danger" />
          </Section>

          <Section title={t("sections.tasks")}>
            <Stat label={t("tasks.running")} value={n(data.tasks.IN_PROGRESS ?? 0)} icon={PlayCircle} />
            <Stat label={t("tasks.completed")} value={n(data.tasks.COMPLETED ?? 0)} icon={CheckCircle2} tone="success" />
            <Stat label={t("tasks.failed")} value={n(data.tasks.FAILED ?? 0)} icon={CircleX} tone="danger" />
            <Stat label={t("tasks.awaitingApproval")} value={n(data.tasks_awaiting_approval)} icon={ShieldCheck} tone="warning" />
            <Stat
              label={t("tasks.successRate")}
              value={data.task_success_rate === null ? t("tasks.noData") : `${n(data.task_success_rate)}%`}
              icon={Sigma}
            />
          </Section>

          <Section title={t("sections.resources")}>
            <Stat label={t("resources.projects")} value={n(data.projects)} icon={FolderGit2} />
            <Stat label={t("resources.repositories")} value={n(data.repositories)} icon={GitBranch} />
            <Stat label={t("resources.tokens")} value={n(data.tokens_total)} icon={Hourglass} hint={t("resources.realOnly")} />
            <Stat label={t("resources.cost")} value={usd(data.cost_usd_total)} icon={CircleDollarSign} hint={t("resources.realOnly")} />
          </Section>

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
            <Card className="xl:col-span-2">
              <CardHeader>
                <CardTitle>{t("usageChart.title")}</CardTitle>
              </CardHeader>
              <CardContent>
                {hasUsage ? (
                  <div className="h-64" dir="ltr">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={data.usage_series} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                        <XAxis dataKey="date" tickFormatter={(d: string) => d.slice(5)} stroke="var(--muted-foreground)" fontSize={12}
                          reversed={locale === "ar"} />
                        <YAxis stroke="var(--muted-foreground)" fontSize={12} tickFormatter={n} orientation={locale === "ar" ? "right" : "left"} />
                        <Tooltip
                          contentStyle={{ background: "var(--card)", border: "1px solid var(--border)", borderRadius: 8 }}
                          formatter={(v) => [n(Number(v)), t("usageChart.tokens")]}
                        />
                        <Area type="monotone" dataKey="tokens" stroke="var(--primary)" fill="var(--primary)" fillOpacity={0.15} />
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <div className="flex h-64 flex-col items-center justify-center gap-2 text-center text-sm text-muted-foreground">
                    <Activity className="size-6" aria-hidden />
                    <p className="max-w-sm">{t("usageChart.empty")}</p>
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>{t("activity.title")}</CardTitle>
              </CardHeader>
              <CardContent>
                {data.recent_activity.length === 0 ? (
                  <p className="py-8 text-center text-sm text-muted-foreground">{t("activity.empty")}</p>
                ) : (
                  <ul className="flex max-h-96 flex-col divide-y overflow-y-auto pe-1" data-testid="recent-activity" tabIndex={0} aria-label={t("activity.title")}>
                    {data.recent_activity.map((a) => (
                      <li key={a.id} className="flex items-start gap-3 py-2.5">
                        {a.level === "warning" || a.level === "error" ? (
                          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-label={ta(`levels.${a.level}`)} />
                        ) : (
                          <Clock className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
                        )}
                        <div className="min-w-0">
                          <p className="text-sm">{actionLabel(a.action)}</p>
                          <time className="text-xs text-muted-foreground" dateTime={a.ts}>
                            {new Date(a.ts).toLocaleString(numLocale)}
                          </time>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
