"use client";
import { CalendarClock, History, Pause, Pencil, Play, Plus, Trash2, Workflow as WorkflowIcon, Zap } from "lucide-react";
import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";
import { useState } from "react";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { useCan } from "@/components/providers";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api";
import { type Action, type Automation, type Trigger, useAllWorkflows, useAutomationActions, useAutomationRuns, useAutomations } from "@/lib/automations";
import { useProjects } from "@/lib/tasks";
import { useToasts } from "@/lib/toast";

function useFail() {
  const tc = useTranslations("common");
  const toast = useToasts((s) => s.push);
  return (e: unknown) => toast("error", e instanceof ApiError && e.status === 422 ? e.code : tc("requestFailed", { code: e instanceof ApiError ? e.code : "error" }));
}

export default function OrchestrationPage() {
  const t = useTranslations("orchestration");
  const [tab, setTab] = useState<"workflows" | "automations">("workflows");
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>
      <div role="tablist" aria-label={t("title")} className="flex flex-wrap gap-2 border-b pb-2">
        {(["workflows", "automations"] as const).map((x) => (
          <Button key={x} role="tab" id={`otab-${x}`} aria-selected={tab === x} aria-controls={`opanel-${x}`} data-testid={`otab-${x}`}
            variant={tab === x ? "default" : "ghost"} onClick={() => setTab(x)}>
            {x === "workflows" ? <WorkflowIcon aria-hidden /> : <CalendarClock aria-hidden />}{t(`tabs.${x}`)}
          </Button>
        ))}
      </div>
      <div role="tabpanel" id={`opanel-${tab}`} aria-labelledby={`otab-${tab}`}>
        {tab === "workflows" ? <Workflows /> : <Automations />}
      </div>
    </div>
  );
}

function Workflows() {
  const t = useTranslations("orchestration");
  const fmt = useFormatter();
  const wfs = useAllWorkflows();
  const projects = useProjects();
  const pname = (id: number) => projects.data?.find((p) => p.id === id)?.name ?? `#${id}`;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted-foreground">{t("workflowsHelp")} <Link className="text-primary underline" href="/tasks">{t("toBoard")}</Link></p>
      {wfs.isLoading && <Skeleton className="h-32" aria-busy="true" />}
      {wfs.data?.length === 0 && <Card className="p-8 text-center text-muted-foreground">{t("noWorkflows")}</Card>}
      <ul className="flex flex-col gap-2" data-testid="wf-list">
        {wfs.data?.map((w) => {
          const last = w.plan?.rounds?.[w.plan.rounds.length - 1];
          const waiting = last ? Object.entries(last.waiting ?? {}) : [];
          return (
            <li key={w.id}>
              <Card className="flex flex-col gap-1 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="font-semibold" dir="auto">{w.title}</h2>
                  <span className="rounded-full border px-2 py-0.5 text-xs font-medium">{t.has(`wfStatus.${w.status}`) ? t(`wfStatus.${w.status}`) : w.status}</span>
                </div>
                <p className="text-xs text-muted-foreground"><span dir="auto">{pname(w.project_id)}</span> · {fmt.relativeTime(new Date(w.created_at))}{w.plan?.planner?.is_mock ? ` · ${t("mock")}` : ""}</p>
                <p className="line-clamp-2 text-sm" dir="auto">{w.objective}</p>
                {waiting.length > 0 && <p className="text-xs text-muted-foreground">{t("waiting", { n: waiting.length })}: <span dir="ltr">{waiting.slice(0, 3).map(([k, v]) => `#${k} ${v}`).join("; ")}</span></p>}
                {w.error && <p className="text-xs" role="alert" dir="auto">{w.error}</p>}
                <Link className="self-start text-sm text-primary underline" href={`/tasks?project=${w.project_id}&workflow=${w.id}`}>{t("openTasks")}</Link>
              </Card>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function describe(t: ReturnType<typeof useTranslations>, tr: Trigger): string {
  if (tr.type === "cron") return t("trig.cron", { expr: tr.expr });
  if (tr.type === "interval") return t("trig.interval", { n: tr.minutes });
  return t("trig.event", { action: tr.action });
}

function Automations() {
  const t = useTranslations("orchestration");
  const fmt = useFormatter();
  const can = useCan();
  const list = useAutomations();
  const projects = useProjects();
  const a = useAutomationActions();
  const fail = useFail();
  const toast = useToasts((s) => s.push);
  const [editing, setEditing] = useState<Automation | "new">();
  const [history, setHistory] = useState<Automation>();
  const [deleting, setDeleting] = useState<Automation>();
  const manage = can("automations.manage");
  const pname = (id: number) => projects.data?.find((p) => p.id === id)?.name ?? `#${id}`;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="max-w-3xl text-sm text-muted-foreground">{t("automationsHelp")}</p>
        {manage && <Button onClick={() => setEditing("new")} data-testid="auto-new"><Plus aria-hidden />{t("newAutomation")}</Button>}
      </div>
      {list.data?.length === 0 && <Card className="p-8 text-center text-muted-foreground">{t("noAutomations")}</Card>}
      <ul className="flex flex-col gap-2" data-testid="auto-list">
        {list.data?.map((x) => (
          <li key={x.id}>
            <Card className="flex flex-col gap-2 p-4" data-testid={`auto-${x.id}`}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h2 className="font-semibold" dir="auto">{x.name}</h2>
                <span className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium" data-testid="auto-status">
                  {x.status === "active" ? <Zap className="size-3.5 text-success" aria-hidden /> : <Pause className="size-3.5 text-muted-foreground" aria-hidden />}{t(`autoStatus.${x.status}`)}
                </span>
              </div>
              <p className="text-xs text-muted-foreground"><span dir="auto">{pname(x.project_id)}</span> · <span dir="ltr">{describe(t, x.trigger)}</span> · {t(`act.${x.action.type}`)}</p>
              <p className="text-xs text-muted-foreground">
                {t("lastRun")}: {x.last_run_at ? fmt.relativeTime(new Date(x.last_run_at)) : "—"} · {t("nextRun")}: {x.next_run_at && x.status === "active" ? fmt.dateTime(new Date(x.next_run_at), { dateStyle: "short", timeStyle: "short" }) : "—"}
              </p>
              <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="outline" onClick={() => setHistory(x)} aria-label={`${t("history")}: ${x.name}`}><History aria-hidden />{t("history")}</Button>
                {manage && <>
                  {x.status === "active"
                    ? <Button size="sm" variant="outline" onClick={() => a.pause.mutate(x.id, { onError: fail })} aria-label={`${t("pause")}: ${x.name}`}><Pause aria-hidden />{t("pause")}</Button>
                    : <Button size="sm" variant="outline" onClick={() => a.resume.mutate(x.id, { onError: fail })} aria-label={`${t("resume")}: ${x.name}`}><Play aria-hidden />{t("resume")}</Button>}
                  <Button size="sm" variant="outline" onClick={() => a.run.mutate(x.id, { onSuccess: (r) => toast(r.status === "succeeded" ? "success" : "error", t(`runStatus.${r.status}`)), onError: fail })} aria-label={`${t("runNow")}: ${x.name}`}><Zap aria-hidden />{t("runNow")}</Button>
                  <Button size="sm" variant="outline" onClick={() => setEditing(x)} aria-label={`${t("edit")}: ${x.name}`}><Pencil aria-hidden />{t("edit")}</Button>
                  <Button size="sm" variant="outline" onClick={() => setDeleting(x)} aria-label={`${t("delete")}: ${x.name}`}><Trash2 aria-hidden />{t("delete")}</Button>
                </>}
              </div>
            </Card>
          </li>
        ))}
      </ul>
      {editing && <AutomationDialog automation={editing === "new" ? undefined : editing} onClose={() => setEditing(undefined)} />}
      {history && <HistoryDialog automation={history} onClose={() => setHistory(undefined)} />}
      <ConfirmDialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(undefined)} destructive title={t("deleteTitle")}
        description={t("deleteBody", { name: deleting?.name ?? "" })} confirmLabel={t("delete")} busy={a.remove.isPending}
        onConfirm={() => deleting && a.remove.mutate(deleting.id, { onSuccess: () => setDeleting(undefined), onError: fail })} />
    </div>
  );
}

function AutomationDialog({ automation, onClose }: { automation?: Automation; onClose: () => void }) {
  const t = useTranslations("orchestration");
  const tc = useTranslations("common");
  const projects = useProjects();
  const a = useAutomationActions();
  const fail = useFail();
  const tr = automation?.trigger;
  const act = automation?.action;
  const [name, setName] = useState(automation?.name ?? "");
  const [project, setProject] = useState<string>(automation ? String(automation.project_id) : "");
  const [ttype, setTtype] = useState<Trigger["type"]>(tr?.type ?? "cron");
  const [expr, setExpr] = useState(tr?.type === "cron" ? tr.expr : "0 3 * * *");
  const [minutes, setMinutes] = useState(tr?.type === "interval" ? String(tr.minutes) : "60");
  const [event, setEvent] = useState(tr?.type === "event" ? tr.action : "github.pr_merged");
  const [atype, setAtype] = useState<Action["type"]>(act?.type ?? "workflow");
  const [title, setTitle] = useState(act?.type === "workflow" ? act.title : "");
  const [objective, setObjective] = useState(act?.type === "workflow" ? act.objective : "");
  const pid = project || (projects.data?.[0] ? String(projects.data[0].id) : "");
  const trigger: Trigger = ttype === "cron" ? { type: "cron", expr } : ttype === "interval" ? { type: "interval", minutes: Number(minutes) } : { type: "event", action: event };
  const action: Action = atype === "workflow" ? { type: "workflow", title, objective } : { type: "analyze_project" };
  const busy = a.create.isPending || a.edit.isPending;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-h-[90vh] max-w-xl overflow-y-auto">
        <DialogTitle>{automation ? t("editTitle", { name: automation.name }) : t("newAutomation")}</DialogTitle>
        <DialogDescription>{t("dialogHelp")}</DialogDescription>
        <form className="mt-4 flex flex-col gap-4" onSubmit={(e) => {
          e.preventDefault();
          if (automation) a.edit.mutate({ id: automation.id, name, trigger, action }, { onSuccess: onClose, onError: fail });
          else a.create.mutate({ name, project_id: Number(pid), trigger, action }, { onSuccess: onClose, onError: fail });
        }}>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="au-name">{t("name")}</Label>
            <Input id="au-name" required maxLength={128} value={name} onChange={(e) => setName(e.target.value)} dir="auto" />
          </div>
          {!automation && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="au-project">{t("project")}</Label>
              <NativeSelect id="au-project" required value={pid} onChange={(e) => setProject(e.target.value)}>
                {projects.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </NativeSelect>
            </div>
          )}
          <fieldset className="flex flex-col gap-3 rounded-md border p-3">
            <legend className="px-1 text-sm font-medium">{t("trigger")}</legend>
            <NativeSelect id="au-ttype" aria-label={t("trigger")} value={ttype} onChange={(e) => setTtype(e.target.value as Trigger["type"])}>
              {(["cron", "interval", "event"] as const).map((x) => <option key={x} value={x}>{t(`trigTypes.${x}`)}</option>)}
            </NativeSelect>
            {ttype === "cron" && <div className="flex flex-col gap-1.5"><Label htmlFor="au-expr">{t("cron")}</Label><Input id="au-expr" value={expr} onChange={(e) => setExpr(e.target.value)} dir="ltr" aria-describedby="au-expr-h" /><p id="au-expr-h" className="text-xs text-muted-foreground">{t("cronHelp")}</p></div>}
            {ttype === "interval" && <div className="flex max-w-xs flex-col gap-1.5"><Label htmlFor="au-min">{t("minutes")}</Label><Input id="au-min" type="number" min={5} value={minutes} onChange={(e) => setMinutes(e.target.value)} dir="ltr" /></div>}
            {ttype === "event" && <div className="flex flex-col gap-1.5"><Label htmlFor="au-ev">{t("eventAction")}</Label><Input id="au-ev" value={event} onChange={(e) => setEvent(e.target.value)} dir="ltr" aria-describedby="au-ev-h" /><p id="au-ev-h" className="text-xs text-muted-foreground">{t("eventHelp")}</p></div>}
          </fieldset>
          <fieldset className="flex flex-col gap-3 rounded-md border p-3">
            <legend className="px-1 text-sm font-medium">{t("action")}</legend>
            <NativeSelect id="au-atype" aria-label={t("action")} value={atype} onChange={(e) => setAtype(e.target.value as Action["type"])}>
              {(["workflow", "analyze_project"] as const).map((x) => <option key={x} value={x}>{t(`act.${x}`)}</option>)}
            </NativeSelect>
            {atype === "workflow" && <>
              <div className="flex flex-col gap-1.5"><Label htmlFor="au-title">{t("wfTitle")}</Label><Input id="au-title" required value={title} onChange={(e) => setTitle(e.target.value)} dir="auto" /></div>
              <div className="flex flex-col gap-1.5"><Label htmlFor="au-obj">{t("objective")}</Label><Textarea id="au-obj" required rows={3} value={objective} onChange={(e) => setObjective(e.target.value)} dir="auto" /></div>
            </>}
          </fieldset>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>{tc("cancel")}</Button>
            <Button type="submit" disabled={busy || !pid} data-testid="auto-save">{busy ? tc("saving") : tc("save")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function HistoryDialog({ automation, onClose }: { automation: Automation; onClose: () => void }) {
  const t = useTranslations("orchestration");
  const tc = useTranslations("common");
  const fmt = useFormatter();
  const runs = useAutomationRuns(automation.id);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-h-[90vh] max-w-xl overflow-y-auto">
        <DialogTitle>{t("historyTitle", { name: automation.name })}</DialogTitle>
        <DialogDescription>{t("historyHelp")}</DialogDescription>
        {runs.data?.length === 0 && <p className="mt-4 text-sm text-muted-foreground">{t("noRuns")}</p>}
        <ol className="mt-4 flex flex-col gap-2" data-testid="auto-runs">
          {runs.data?.map((r) => (
            <li key={r.id} className="rounded-md border p-2 text-sm">
              <p className="font-medium">{t(`runStatus.${r.status}`)} · {t(`runTrigger.${r.trigger}`)}</p>
              <p className="text-xs text-muted-foreground">{fmt.dateTime(new Date(r.started_at), { dateStyle: "medium", timeStyle: "short" })}</p>
              {r.detail && <p className="text-xs" dir="auto">{r.detail}</p>}
            </li>
          ))}
        </ol>
      </DialogContent>
    </Dialog>
  );
}
