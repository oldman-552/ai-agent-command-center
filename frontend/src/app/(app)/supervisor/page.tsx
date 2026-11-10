"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, AlertOctagon, AlertTriangle, CheckCircle2, Info, Play, ShieldOff, Wrench } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import { useCan } from "@/components/providers";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { useToasts } from "@/lib/toast";
import { cn } from "@/lib/utils";

type Health = { agents: Record<string, number>; active_executions: Record<string, number>; open_findings: Record<string, number>;
  isolated_agents: number; last_sweep_at: string | null; autonomous_actions: string[] };
type Finding = { id: number; kind: string; severity: "info" | "warning" | "critical"; subject_type: string; subject_id: string;
  recommendation: string; action: { action: string; reversible?: string } | null; status: string; last_seen_at: string };
type ActionLog = { id: number; ts: string; action: string; actor: string; details: Record<string, unknown> };

const fetchHealth = () => api<Health>("/api/supervisor/health");
const fetchFindings = () => api<Finding[]>("/api/supervisor/findings?status=open");
const fetchActions = () => api<ActionLog[]>("/api/supervisor/actions?limit=30");
type SweepResult = { findings: number; actions: unknown[] };
const runSweep = () => api<SweepResult>("/api/supervisor/run", { method: "POST" });
const resolveFinding = (id: number) => api<Finding>(`/api/supervisor/findings/${id}/resolve`, { method: "POST" });
const SEV = { critical: { icon: AlertOctagon, tone: "text-destructive" }, warning: { icon: AlertTriangle, tone: "text-warning" },
  info: { icon: Info, tone: "text-primary" } } as const;
const sum = (o: Record<string, number>) => Object.values(o).reduce((a, b) => a + b, 0);

export default function SupervisorPage() {
  const t = useTranslations("supervisor");
  const tc = useTranslations("common");
  const fmt = useFormatter();
  const can = useCan();
  const qc = useQueryClient();
  const toast = useToasts((s) => s.push);
  const health = useQuery({ queryKey: ["sv-health"], queryFn: fetchHealth, refetchInterval: 10_000 });
  const findings = useQuery({ queryKey: ["sv-findings"], queryFn: fetchFindings, refetchInterval: 10_000 });
  const actions = useQuery({ queryKey: ["sv-actions"], queryFn: fetchActions, refetchInterval: 10_000 });
  const refresh = () => ["sv-health", "sv-findings", "sv-actions"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const run = useMutation({ mutationFn: runSweep,
    onSuccess: (r) => { toast("success", t("swept", { n: r.findings, a: r.actions.length })); refresh(); } });
  const resolve = useMutation({ mutationFn: resolveFinding, onSuccess: refresh });
  const label = (k: string) => (t.has(`kinds.${k}`) ? t(`kinds.${k}`) : k);
  const h = health.data;

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{t("title")}</h1>
          <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
        </div>
        {can("agents.control") && (
          <Button onClick={() => run.mutate()} disabled={run.isPending} data-testid="sv-run"><Play aria-hidden />{t("runNow")}</Button>
        )}
      </div>

      {health.isLoading && <Skeleton className="h-24" aria-busy="true" />}
      {h && (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="sv-health">
          {([
            ["agents", sum(h.agents), Activity],
            ["executions", sum(h.active_executions), Play],
            ["findings", sum(h.open_findings), AlertTriangle],
            ["isolated", h.isolated_agents, ShieldOff],
          ] as const).map(([k, v, Icon]) => (
            <li key={k}>
              <Card className="flex items-center gap-3 p-4">
                <Icon className="size-5 text-primary" aria-hidden />
                <p className="flex flex-col"><span className="text-xs text-muted-foreground">{t(`health.${k}`)}</span><span className="text-xl font-semibold tabular-nums">{v}</span></p>
              </Card>
            </li>
          ))}
        </ul>
      )}
      {h && (
        <p className="text-xs text-muted-foreground">
          {h.last_sweep_at ? t("lastSweep", { when: fmt.relativeTime(new Date(h.last_sweep_at)) }) : t("neverSwept")} ·{" "}
          {h.autonomous_actions.length ? t("autonomous", { list: h.autonomous_actions.map((a) => t.has(`actions.${a}`) ? t(`actions.${a}`) : a).join("، ") }) : t("advisoryOnly")}
        </p>
      )}

      <section aria-labelledby="h-findings" className="flex flex-col gap-3">
        <h2 id="h-findings" className="text-lg font-semibold">{t("findings")}</h2>
        {findings.isError && <Card role="alert" className="p-4 text-destructive">{tc("error")}</Card>}
        {findings.data?.length === 0 && (
          <Card className="flex items-center justify-center gap-2 p-6 text-muted-foreground"><CheckCircle2 className="size-5 text-success" aria-hidden />{t("allClear")}</Card>
        )}
        {!!findings.data?.length && <ul className="flex flex-col gap-2" data-testid="sv-findings">
          {findings.data.map((f) => {
            const s = SEV[f.severity] ?? SEV.info;
            return (
              <li key={f.id}>
                <Card className="flex flex-col gap-1.5 p-4 text-sm">
                  <span className="flex flex-wrap items-center gap-2">
                    <s.icon className={cn("size-4", s.tone)} aria-hidden />
                    <span className="font-semibold">{label(f.kind)}</span>
                    <span className="text-xs text-muted-foreground">{t(`severity.${f.severity}`)} · {t(`subject.${f.subject_type}`)} <span dir="ltr">#{f.subject_id.slice(0, 12)}</span></span>
                    <span className="ms-auto text-xs text-muted-foreground">{fmt.relativeTime(new Date(f.last_seen_at))}</span>
                  </span>
                  <span className="text-muted-foreground" dir="auto">{f.recommendation}</span>
                  {f.action && (
                    <span className="flex items-center gap-1.5 text-xs"><Wrench className="size-3.5 text-primary" aria-hidden />
                      {t("taken", { action: t.has(`actions.${f.action.action}`) ? t(`actions.${f.action.action}`) : f.action.action })}</span>
                  )}
                  {can("agents.control") && (
                    <Button variant="outline" size="sm" className="self-end" disabled={resolve.isPending} onClick={() => resolve.mutate(f.id)}>{t("resolve")}</Button>
                  )}
                </Card>
              </li>
            );
          })}
        </ul>}
      </section>

      <section aria-labelledby="h-actions" className="flex flex-col gap-3">
        <h2 id="h-actions" className="text-lg font-semibold">{t("actionLog")}</h2>
        {actions.data?.length === 0 && <Card className="p-6 text-center text-muted-foreground">{t("noActions")}</Card>}
        {actions.data && actions.data.length > 0 && (
          <Card className="overflow-x-auto" tabIndex={0} aria-label={t("actionLog")}>
            <table className="w-full min-w-[560px] text-sm" data-testid="sv-actions">
              <thead className="border-b bg-muted/50 text-xs text-muted-foreground">
                <tr>{(["time", "action", "actor"] as const).map((c) => <th key={c} scope="col" className="px-4 py-2 text-start font-medium">{t(`columns.${c}`)}</th>)}</tr>
              </thead>
              <tbody className="divide-y">
                {actions.data.map((a) => (
                  <tr key={a.id}>
                    <td className="whitespace-nowrap px-4 py-2 tabular-nums">{fmt.dateTime(new Date(a.ts), { dateStyle: "short", timeStyle: "medium" })}</td>
                    <td className="px-4 py-2 font-mono text-xs" dir="ltr">{a.action}</td>
                    <td className="px-4 py-2 font-mono text-xs" dir="ltr">{a.actor}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}
      </section>
    </div>
  );
}
