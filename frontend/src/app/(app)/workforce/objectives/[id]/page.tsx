"use client";
import { AlertTriangle, ArrowLeft, ArrowRight, Ban, Download, FileText, History as HistoryIcon, Info, ListChecks, RefreshCw } from "lucide-react";
import { useFormatter, useLocale, useTranslations } from "next-intl";
import Link from "next/link";
import { useParams, usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useCan } from "@/components/providers";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { LifecycleBadge, MockBadge, ModeBadge, ObjectiveStatus, useActor, useFail, useReason, WorkforceTabs } from "@/components/workforce/shared";
import { TaskDialog } from "@/components/workforce/task-dialog";
import { ApiError } from "@/lib/api";
import { useToasts } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { type History, type ObjectiveFull, type Report, type ReportClass, useHistory, useObjective, useReport, useWorkforceActions } from "@/lib/workforce";

export default function ObjectiveRoute() {
  return <Suspense><ObjectivePage /></Suspense>;
}

const VIEWS = [
  { key: "plan", icon: ListChecks },
  { key: "report", icon: FileText },
  { key: "history", icon: HistoryIcon },
] as const;
type View = (typeof VIEWS)[number]["key"];

function ObjectivePage() {
  const t = useTranslations("workforce.objective");
  const tc = useTranslations("common");
  const id = Number(useParams<{ id: string }>().id);
  const sp = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const can = useCan();
  const Back = useLocale() === "ar" ? ArrowRight : ArrowLeft;
  const set = (patch: Record<string, string | undefined>) => {
    const next = new URLSearchParams(sp.toString());
    for (const [k, v] of Object.entries(patch)) (v ? next.set(k, v) : next.delete(k));
    router.replace(`${pathname}?${next}`, { scroll: false });
  };
  const view: View = (["plan", "report", "history"] as const).find((v) => v === sp.get("view")) ?? "plan";
  const openTask = sp.get("task") ? Number(sp.get("task")) : undefined;
  const objective = useObjective(id);
  const a = useWorkforceActions();
  const fail = useFail();
  const toast = useToasts((s) => s.push);
  const [cancel, setCancel] = useState(false);
  const o = objective.data;
  const active = o && (o.status === "planning" || o.status === "running");
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <WorkforceTabs />
      <Link href="/workforce" className="flex w-fit items-center gap-1.5 text-sm font-medium text-primary underline underline-offset-4">
        <Back className="size-4" aria-hidden />{t("back")}
      </Link>
      {objective.isLoading && <Skeleton className="h-48" aria-busy="true" />}
      {objective.isError && (
        <Card role="alert" className="p-6 text-center">
          <h1 className="text-lg font-semibold">{objective.error instanceof ApiError && objective.error.status === 404 ? t("notFound") : tc("error")}</h1>
        </Card>
      )}
      {o && (
        <>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex min-w-0 flex-col gap-2">
              <h1 className="text-2xl font-bold" dir="auto" data-testid="wf-objective-title">{o.title}</h1>
              <div className="flex flex-wrap items-center gap-2">
                <ObjectiveStatus status={o.status} />
                {(o.manager?.is_mock || o.report?.is_mock) && <MockBadge />}
                <span className="font-mono text-xs text-muted-foreground" dir="ltr">#{o.id}</span>
                <span className="text-sm text-muted-foreground" data-testid="wf-objective-progress">
                  {o.status === "planning" ? t("planning") : t("progress", { done: o.progress.completed, total: o.progress.total })}
                </span>
              </div>
            </div>
            {can("workforce.review") && (
              <div className="flex flex-wrap gap-2">
                {o.status !== "planning" && (
                  <Button variant="outline" size="sm" disabled={a.tick.isPending} data-testid="wf-objective-tick"
                    onClick={() => a.tick.mutate(o.id, { onError: fail, onSuccess: () => toast("success", t("ticked")) })}>
                    <RefreshCw aria-hidden />{t("tick")}
                  </Button>
                )}
                {active && <Button variant="outline" size="sm" onClick={() => setCancel(true)} data-testid="wf-objective-cancel"><Ban aria-hidden />{t("cancel")}</Button>}
              </div>
            )}
          </div>
          {o.error && (
            <div role="alert" className="flex gap-3 rounded-md border border-destructive p-3 text-sm" data-testid="wf-objective-error">
              <AlertTriangle className="mt-0.5 size-5 shrink-0 text-destructive" aria-hidden /><p dir="auto">{o.error}</p>
            </div>
          )}

          <div role="group" aria-label={t("views")} className="flex w-fit flex-wrap rounded-md border p-0.5">
            {VIEWS.map(({ key, icon: Icon }) => (
              <Button key={key} variant={view === key ? "secondary" : "ghost"} size="sm" aria-pressed={view === key}
                onClick={() => set({ view: key === "plan" ? undefined : key })} data-testid={`wf-view-${key}`}>
                <Icon aria-hidden />{t(`view.${key}`)}
              </Button>
            ))}
          </div>

          {view === "plan" && <Plan o={o} onOpen={(taskId) => set({ task: String(taskId) })} />}
          {view === "report" && <ReportView o={o} />}
          {view === "history" && <HistoryView id={o.id} />}
        </>
      )}
      {openTask && <TaskDialog id={openTask} onClose={() => set({ task: undefined })} />}
      {o && cancel && <CancelDialog id={o.id} onClose={() => setCancel(false)} />}
    </div>
  );
}

function CancelDialog({ id, onClose }: { id: number; onClose: () => void }) {
  const t = useTranslations("workforce.objective");
  const tc = useTranslations("common");
  const a = useWorkforceActions();
  const fail = useFail();
  const [reason, setReason] = useState("");
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")}>
        <DialogTitle>{t("cancelTitle")}</DialogTitle>
        <DialogDescription>{t("cancelHelp")}</DialogDescription>
        <form className="mt-4 flex flex-col gap-4" onSubmit={(e) => {
          e.preventDefault();
          a.cancelObjective.mutate({ id, reason: reason.trim() }, { onError: fail, onSuccess: onClose });
        }}>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wf-cancel-reason">{t("cancelReason")}</Label>
            <Input id="wf-cancel-reason" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} dir="auto" />
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>{tc("back")}</Button>
            <Button type="submit" variant="destructive" disabled={a.cancelObjective.isPending} data-testid="wf-objective-cancel-confirm">{t("cancel")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Plan({ o, onOpen }: { o: ObjectiveFull; onOpen: (id: number) => void }) {
  const t = useTranslations("workforce.objective");
  const reason = useReason();
  const deps = new Map<number, number[]>();
  for (const d of o.dependencies) deps.set(d.task_id, [...(deps.get(d.task_id) ?? []), d.depends_on_id]);
  const an = o.analysis;
  const lists = an ? ([["deliverables", an.deliverables], ["assumptions", an.assumptions], ["risks", an.risks], ["openQuestions", an.open_questions]] as const) : [];
  return (
    <div className="flex flex-col gap-6">
      <Card className="flex flex-col gap-2 p-5">
        <h2 className="text-lg font-semibold">{t("prompt")}</h2>
        <pre className="whitespace-pre-wrap break-words font-sans text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" dir="auto" data-testid="wf-objective-prompt">{o.prompt}</pre>
        {o.prompt_redacted && (
          <p role="note" className="flex gap-2 text-sm" data-testid="wf-prompt-redacted"><Info className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />{t("redacted")}</p>
        )}
        <p className="text-xs text-muted-foreground">{o.review_policy === "human" ? t("policyHuman") : t("policyManager")}</p>
      </Card>

      {o.status === "planning" && <Card role="status" className="p-5 text-sm" data-testid="wf-planning">{t("planningHelp")}</Card>}

      {an && (
        <Card className="flex flex-col gap-3 p-5" data-testid="wf-analysis">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold">{t("analysis")}</h2>
            {o.manager?.is_mock && <MockBadge short />}
            {o.manager && <span className="text-xs text-muted-foreground">{t("writtenBy", { model: o.manager.model })}</span>}
          </div>
          <p className="whitespace-pre-wrap text-sm" dir="auto">{an.understanding}</p>
          {lists.filter(([, items]) => items.length > 0).map(([key, items]) => (
            <div key={key} className="text-sm">
              <h3 className="font-medium">{t(`analysisParts.${key}`)}</h3>
              <ul className="ms-5 list-disc">{items.map((x, i) => <li key={i}><bdi>{x}</bdi></li>)}</ul>
            </div>
          ))}
        </Card>
      )}

      {o.tasks.length > 0 && (
        <section aria-labelledby="wf-plan-tasks-h" className="flex flex-col gap-3">
          <h2 id="wf-plan-tasks-h" className="text-lg font-semibold">{t("tasks", { n: o.tasks.length })}</h2>
          {o.plan_summary && <p className="text-sm text-muted-foreground" dir="auto">{o.plan_summary}</p>}
          <ol className="flex flex-col gap-2" data-testid="wf-plan-tasks">
            {o.tasks.map((task) => (
              <li key={task.id}>
                <button type="button" onClick={() => onOpen(task.id)} data-testid={`wf-plan-task-${task.id}`}
                  className="flex w-full flex-col gap-2 rounded-lg border bg-card p-4 text-start shadow-sm transition-colors duration-200 hover:border-primary focus-visible:outline-2 focus-visible:outline-ring">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-xs text-muted-foreground" dir="ltr">#{task.id}</span>
                    <LifecycleBadge state={task.lifecycle} />
                    {task.mode && <ModeBadge mode={task.mode} />}
                    {task.worker && <span className="text-xs text-muted-foreground"><span className="font-mono" dir="ltr">{task.worker.label}</span> <span dir="auto">{task.worker.name}</span></span>}
                  </span>
                  <span className="font-medium" dir="auto">{task.title}</span>
                  <span className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                    {(deps.get(task.id) ?? []).length > 0 && <span>{t("after", { ids: (deps.get(task.id) ?? []).map((x) => `#${x}`).join(", ") })}</span>}
                    {task.waiting && <span>{t("waiting", { reason: reason(task.waiting.reason) })}</span>}
                    {task.needs_human_review && <span className="font-medium text-foreground">{t("needsPerson")}</span>}
                    {task.critical_operation && <span>{t("critical")}</span>}
                  </span>
                </button>
              </li>
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}

const CLASS_TONE: Record<ReportClass, string> = {
  verified: "text-success", human_approved: "text-success", worker_proposal: "text-primary", unverified_claim: "text-warning",
  failed: "text-destructive", incomplete: "text-muted-foreground",
};

function ReportView({ o }: { o: ObjectiveFull }) {
  const t = useTranslations("workforce.report");
  const tc = useTranslations("common");
  const report = useReport(o.id, true);
  const missing = report.error instanceof ApiError && report.error.status === 404;
  if (report.isLoading) return <Skeleton className="h-64" aria-busy="true" />;
  if (missing) return <Card className="p-8 text-center text-sm text-muted-foreground" data-testid="wf-no-report">{t("none")}</Card>;
  if (report.isError || !report.data) return <Card role="alert" className="p-6 text-center text-destructive">{tc("error")}</Card>;
  return <ReportBody r={report.data} objectiveId={o.id} />;
}

function ReportBody({ r, objectiveId }: { r: Report; objectiveId: number }) {
  const t = useTranslations("workforce.report");
  const tw = useTranslations("workforce");
  const actor = useActor();
  const fmt = useFormatter();
  const c = r.content;
  return (
    <div className="flex flex-col gap-6" data-testid="wf-report">
      <Card className="flex flex-col gap-3 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold">{t("title", { version: r.version })}</h2>
            <span className="rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium" data-testid="wf-report-status">{t(`status.${r.status}`)}</span>
            {r.is_mock && <MockBadge />}
          </div>
          <a href={`/api/workforce/objectives/${objectiveId}/report.md?version=${r.version}`} download={`report-${objectiveId}-v${r.version}.md`}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-md border px-3 text-sm font-medium hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring">
            <Download className="size-4" aria-hidden />{t("download")}
          </a>
        </div>
        <p className="text-sm text-muted-foreground">{t("generated", { when: fmt.dateTime(new Date(c.generated_at), { dateStyle: "medium", timeStyle: "short" }) })}</p>
        {r.is_mock && <p role="note" className="flex gap-2 text-sm"><AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />{t("mockNote")}</p>}
        <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3" data-testid="wf-report-counts">
          {c.classes.map((k) => (
            <li key={k} className="flex items-center justify-between gap-3 rounded-md border p-3 text-sm" data-testid={`wf-count-${k}`}>
              <span className={cn("font-medium", c.counts[k] === 0 && "text-muted-foreground")}>{t(`classes.${k}`)}</span>
              <span className={cn("text-lg font-bold tabular-nums", c.counts[k] > 0 ? CLASS_TONE[k] : "text-muted-foreground")}>{c.counts[k]}</span>
            </li>
          ))}
        </ul>
        <p className="text-xs text-muted-foreground">{t("classesHelp")}</p>
      </Card>

      <Card className="flex flex-col gap-2 p-5" data-testid="wf-report-facts">
        <h2 className="text-lg font-semibold">{t("facts")}</h2>
        <p className="text-xs text-muted-foreground">{t("factsHelp")}</p>
        <ul className="ms-5 flex list-disc flex-col gap-1 text-sm" data-testid="wf-report-facts-summary">
          <li>{t("fact.completed", { done: c.completed, total: c.tasks, failed: c.counts.failed, incomplete: c.counts.incomplete })}</li>
          <li>{t("fact.accepted", { people: c.counts.human_approved, manager: c.counts.worker_proposal + c.counts.unverified_claim })}</li>
          {c.counts.verified === 0 && <li>{t("fact.nothingVerified")}</li>}
          {c.planned_by?.is_mock && <li>{t("fact.mockPlan")}</li>}
          {c.is_mock && <li>{t("fact.mock")}</li>}
        </ul>
        <details>
          <summary className="cursor-pointer text-xs font-medium text-primary">{t("factsOriginal", { n: c.facts.length })}</summary>
          <ul className="ms-5 mt-2 flex list-disc flex-col gap-1 text-sm" lang="en" dir="ltr">{c.facts.map((f, i) => <li key={i}>{f}</li>)}</ul>
        </details>
      </Card>

      {c.classes.filter((k) => c.counts[k] > 0).map((k) => (
        <section key={k} aria-labelledby={`wf-class-${k}`} className="flex flex-col gap-3" data-testid={`wf-class-${k}`}>
          <h2 id={`wf-class-${k}`} className="text-lg font-semibold">{t(`classes.${k}`)} <span className="tabular-nums text-muted-foreground">({c.counts[k]})</span></h2>
          <p className="text-sm text-muted-foreground">{t(`classHelp.${k}`)}</p>
          <ul className="flex flex-col gap-3">
            {c.items.filter((i) => i.class === k).map((i) => (
              <li key={i.task_id}>
                <Card className="flex flex-col gap-2 p-4 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link href={`/workforce/tasks?task=${i.task_id}`} className="font-mono text-xs text-primary underline underline-offset-4" dir="ltr">#{i.task_id}</Link>
                    <span className="font-medium" dir="auto">{i.title}</span>
                    {i.is_mock && <MockBadge short />}
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {i.worker ?? "—"}{i.mode ? ` · ${tw(`mode.${i.mode}`)}` : ""} · {t("attempts", { n: i.attempts })} · {t("versions", { n: i.versions })}
                    {i.submission?.decided_by ? ` · ${t("acceptedBy", { who: actor(i.submission.decided_by) })}` : ""}
                  </p>
                  {i.reason && <p className="text-xs" dir="auto">{i.reason}</p>}
                  {i.excerpt && (
                    <details>
                      <summary className="cursor-pointer text-xs font-medium text-primary">{t("showExcerpt")}</summary>
                      <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted p-3 font-sans text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" dir="auto" tabIndex={0}
                        aria-label={i.title}>{i.excerpt}</pre>
                    </details>
                  )}
                </Card>
              </li>
            ))}
          </ul>
        </section>
      ))}

      <Card className="flex flex-col gap-2 p-5" data-testid="wf-report-narrative">
        <h2 className="text-lg font-semibold">{t("narrative")}</h2>
        <p className="text-xs font-medium">{t("narrativeHelp")}</p>
        {c.narrative ? (
          <>
            <p className="whitespace-pre-wrap text-sm" dir="auto">{c.narrative.summary}</p>
            {c.narrative.next_steps.length > 0 && <ul className="ms-5 list-disc text-sm">{c.narrative.next_steps.map((s, i) => <li key={i}><bdi>{s}</bdi></li>)}</ul>}
            <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              {t("narrativeBy", { model: c.narrative.written_by.model })}{c.narrative.written_by.is_mock && <MockBadge short />}
            </p>
          </>
        ) : <p className="text-sm text-muted-foreground" dir="auto">{t("noNarrative")}{c.narrative_error ? ` (${c.narrative_error})` : ""}</p>}
      </Card>
    </div>
  );
}

function HistoryView({ id }: { id: number }) {
  const tc = useTranslations("common");
  const history = useHistory(id, true);
  if (history.isLoading) return <Skeleton className="h-64" aria-busy="true" />;
  if (history.isError || !history.data) return <Card role="alert" className="p-6 text-center text-destructive">{tc("error")}</Card>;
  return <HistoryBody h={history.data} />;
}

function HistoryBody({ h }: { h: History }) {
  const t = useTranslations("workforce.history");
  const tw = useTranslations("workforce");
  const te = useTranslations("executions.statuses");
  const fmt = useFormatter();
  const actor = useActor();
  const time = (s: string | null) => (s ? fmt.dateTime(new Date(s), { dateStyle: "short", timeStyle: "medium" }) : "—");
  return (
    <div className="flex flex-col gap-6" data-testid="wf-history">
      <Card className="flex flex-col gap-2 p-5 text-sm">
        <h2 className="text-lg font-semibold">{t("ids")}</h2>
        <dl className="grid gap-x-4 gap-y-1 sm:grid-cols-[12rem_1fr]">
          <dt className="text-muted-foreground">{t("objectiveId")}</dt><dd><span className="font-mono" dir="ltr">{h.objective_id}</span></dd>
          <dt className="text-muted-foreground">{t("threadId")}</dt><dd className="break-all"><span className="font-mono text-xs" dir="ltr">{h.thread_id}</span></dd>
        </dl>
        <p className="text-xs text-muted-foreground">{t("usageNote")}</p>
      </Card>

      <section aria-labelledby="wf-steps-h" className="flex flex-col gap-3">
        <h2 id="wf-steps-h" className="text-lg font-semibold">{t("managerSteps")}</h2>
        <Card className="overflow-x-auto p-0">
          <table className="w-full min-w-[40rem] text-sm" data-testid="wf-history-steps">
            <caption className="sr-only">{t("managerSteps")}</caption>
            <thead className="text-start text-xs text-muted-foreground">
              <tr className="border-b">
                {(["step", "status", "model", "started", "duration", "usage"] as const).map((k) => <th key={k} scope="col" className="px-4 py-2 text-start font-medium">{t(`cols.${k}`)}</th>)}
              </tr>
            </thead>
            <tbody className="divide-y">
              {h.manager_steps.map((s) => (
                <tr key={s.id}>
                  <td className="px-4 py-2 font-medium">{t(`step.${s.kind}`)}{s.task_id ? <>{" "}<span className="font-mono text-xs text-muted-foreground" dir="ltr">#{s.task_id}</span></> : null}</td>
                  <td className="px-4 py-2">{t(`stepStatus.${s.status}`)}{s.error ? <span className="block max-w-56 truncate text-xs text-muted-foreground" lang="en" dir="ltr" title={s.error}>{s.error}</span> : null}</td>
                  <td className="px-4 py-2"><span className="font-mono text-xs" dir="ltr">{s.model ?? "—"}</span>{s.is_mock && <MockBadge short className="ms-2" />}</td>
                  <td className="px-4 py-2 text-xs text-muted-foreground">{time(s.started_at)}</td>
                  <td className="px-4 py-2 tabular-nums">{s.duration_ms === null ? "—" : t("ms", { n: s.duration_ms })}</td>
                  <td className="px-4 py-2 text-xs">{s.usage_reported && s.usage ? t("tokens", { input: s.usage.input_tokens ?? 0, output: s.usage.output_tokens ?? 0 }) : t("notReported")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </section>

      <section aria-labelledby="wf-htasks-h" className="flex flex-col gap-3">
        <h2 id="wf-htasks-h" className="text-lg font-semibold">{t("tasks")}</h2>
        <ul className="flex flex-col gap-3">
          {h.tasks.map((task) => (
            <li key={task.id}>
              <Card className="flex flex-col gap-3 p-4 text-sm" data-testid={`wf-history-task-${task.id}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs text-muted-foreground" dir="ltr">#{task.id}</span>
                  <span className="font-medium" dir="auto">{task.title}</span>
                  <LifecycleBadge state={task.lifecycle} />
                  {task.mode && <ModeBadge mode={task.mode} />}
                  {task.worker && <span className="font-mono text-xs" dir="ltr">{task.worker.label}</span>}
                </div>
                {task.executions.length > 0 && (
                  <div className="flex flex-col gap-2">
                    <h3 className="text-xs font-semibold text-muted-foreground">{t("executions")}</h3>
                    <ul className="flex flex-col gap-2">
                      {task.executions.map((e) => (
                        <li key={e.id} className="rounded-md border p-3 text-xs" data-testid="wf-history-execution">
                          <p className="flex flex-wrap items-center gap-x-3 gap-y-1">
                            <span className="font-medium">{te.has(e.status) ? te(e.status) : e.status}</span>
                            {e.is_mock && <MockBadge short />}
                            <span>{t("tries", { n: e.attempts })}</span>
                            <span className="text-muted-foreground">{time(e.started_at)} – {time(e.ended_at)}</span>
                          </p>
                          <dl className="mt-2 grid gap-x-3 gap-y-1 sm:grid-cols-[11rem_1fr]">
                            <dt className="text-muted-foreground">{t("executionId")}</dt><dd className="break-all"><span className="font-mono" dir="ltr">{e.id}</span></dd>
                            <dt className="text-muted-foreground">{t("idempotencyKey")}</dt><dd className="break-all"><span className="font-mono" dir="ltr">{e.idempotency_key ?? "—"}</span></dd>
                            <dt className="text-muted-foreground">{t("providerRequest")}</dt><dd className="break-all"><span className="font-mono" dir="ltr">{e.provider_request_id ?? "—"}</span></dd>
                            <dt className="text-muted-foreground">{t("usage")}</dt>
                            <dd data-testid="wf-history-usage">{e.usage_reported && e.usage ? t("tokens", { input: e.usage.input_tokens ?? 0, output: e.usage.output_tokens ?? 0 }) : t("notReported")}</dd>
                          </dl>
                          {e.provider_errors.length > 0 && (
                            <ul className="mt-2 flex flex-col gap-1">
                              {e.provider_errors.map((x) => (
                                <li key={x.seq}><span className="font-mono" dir="ltr">{x.code}</span> · {x.category} · {x.delivered === false ? t("notDelivered") : x.delivered ? t("delivered") : t("deliveryUnknown")}</li>
                              ))}
                            </ul>
                          )}
                          {e.error && <p className="mt-2 break-words text-destructive" lang="en" dir="ltr">{e.error}</p>}
                          {e.recovered_from && <p className="mt-2 break-words text-muted-foreground" lang="en" dir="ltr">{t("recoveredFrom")}: {e.recovered_from}</p>}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {task.submissions.length > 0 && (
                  <div className="flex flex-col gap-1">
                    <h3 className="text-xs font-semibold text-muted-foreground">{t("submissions")}</h3>
                    <ul className="flex flex-col gap-1 text-xs">
                      {task.submissions.map((s) => (
                        <li key={s.id} className="flex flex-wrap items-center gap-x-3 gap-y-1">
                          <span className="font-medium">{tw("task.version", { n: s.version })}</span>
                          <span>{s.mode === "HUMAN_ASSISTED" ? actor(s.submitted_by) : s.source}</span>
                          {s.is_mock && <MockBadge short />}
                          <span className="font-mono text-muted-foreground" dir="ltr">{s.sha256.slice(0, 12)}</span>
                          <span>{s.decision ? tw(`task.decision.${s.decision}`, { who: actor(s.decided_by) }) : tw("task.decision.none")}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                <details>
                  <summary className="cursor-pointer text-xs font-medium text-primary">{t("transitions", { n: task.transitions.length })}</summary>
                  <ol className="mt-2 flex flex-col gap-1 text-xs">
                    {task.transitions.map((x) => (
                      <li key={x.id} className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <LifecycleBadge state={x.to} /><span className="text-muted-foreground">{actor(x.actor)}</span><span className="text-muted-foreground">{time(x.at)}</span>
                      </li>
                    ))}
                  </ol>
                </details>
              </Card>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
