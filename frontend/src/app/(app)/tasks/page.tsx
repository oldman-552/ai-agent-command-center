"use client";
import { CheckCircle2, ClipboardList, FolderPlus, LayoutGrid, List, Loader2, RotateCcw, Sparkles, Target, XCircle } from "lucide-react";
import { useTranslations } from "next-intl";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { useCan } from "@/components/providers";
import { statusIcon, statusTone, TaskStatusBadge } from "@/components/task-status-badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useAgents } from "@/lib/agents";
import { ApiError } from "@/lib/api";
import {
  BOARD_COLUMNS, type Task, useCreateProject, useProjects, useReview, useSubmitObjective, useTask, useTasks,
  useWorkflow, useWorkflows,
} from "@/lib/tasks";
import { useToasts } from "@/lib/toast";
import { cn } from "@/lib/utils";

export default function TasksPageRoute() {
  return <Suspense><TasksPage /></Suspense>;
}

/** Query-string state so every view (project, objective, board/list, open task) is deep-linkable. */
function useParamState() {
  const sp = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const num = (k: string) => (sp.get(k) ? Number(sp.get(k)) : undefined);
  const set = (patch: Record<string, string | number | undefined>) => {
    const next = new URLSearchParams(sp.toString());
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined || v === "") next.delete(k);
      else next.set(k, String(v));
    }
    router.replace(`${pathname}?${next}`, { scroll: false });
  };
  return { projectId: num("project"), workflowId: num("workflow"), taskId: num("task"), view: sp.get("view") === "list" ? "list" : "board", set };
}

function useErrorText() {
  const tc = useTranslations("common");
  const t = useTranslations("tasks");
  return (e: unknown) => {
    const code = e instanceof ApiError ? e.code : "network";
    return t.has(`errors.${code.split(":")[0]}`) ? t(`errors.${code.split(":")[0]}`) : tc("requestFailed", { code });
  };
}

function TasksPage() {
  const t = useTranslations("tasks");
  const tc = useTranslations("common");
  const can = useCan();
  const { projectId, workflowId, taskId, view, set } = useParamState();
  const projects = useProjects();
  const workflows = useWorkflows(projectId);
  const workflow = useWorkflow(workflowId);
  const tasks = useTasks({ projectId, workflowId });
  const agents = useAgents({});
  const agentName = useMemo(() => new Map((agents.data ?? []).map((a) => [a.id, a.name])), [agents.data]);
  const [dialog, setDialog] = useState<"project" | "objective" | null>(null);

  const byStatus = useMemo(() => {
    const m = new Map<string, Task[]>(BOARD_COLUMNS.map((c) => [c, []]));
    for (const task of tasks.data ?? []) m.get(task.status)?.push(task);
    return m;
  }, [tasks.data]);

  return (
    <div className="mx-auto flex max-w-[100rem] flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{t("title")}</h1>
          <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {can("projects.write") && (
            <Button variant="outline" onClick={() => setDialog("project")} data-testid="new-project"><FolderPlus aria-hidden />{t("newProject")}</Button>
          )}
          {can("workflows.run") && (
            <Button onClick={() => setDialog("objective")} disabled={!projects.data?.length} data-testid="new-objective"><Target aria-hidden />{t("newObjective")}</Button>
          )}
        </div>
      </div>

      <Card className="flex flex-wrap items-end gap-3 p-4">
        <div className="flex min-w-48 flex-1 flex-col gap-1.5">
          <Label htmlFor="f-project">{t("filters.project")}</Label>
          <NativeSelect id="f-project" value={projectId ?? ""} onChange={(e) => set({ project: e.target.value || undefined, workflow: undefined, task: undefined })}>
            <option value="">{tc("all")}</option>
            {projects.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </NativeSelect>
        </div>
        <div className="flex min-w-48 flex-1 flex-col gap-1.5">
          <Label htmlFor="f-workflow">{t("filters.objective")}</Label>
          <NativeSelect id="f-workflow" value={workflowId ?? ""} onChange={(e) => set({ workflow: e.target.value || undefined, task: undefined })}>
            <option value="">{tc("all")}</option>
            {workflows.data?.map((w) => <option key={w.id} value={w.id}>#{w.id} {w.title} — {t(`workflowStatus.${w.status}`)}</option>)}
          </NativeSelect>
        </div>
        <div role="group" aria-label={t("view.label")} className="flex rounded-md border p-0.5">
          {(["board", "list"] as const).map((v) => {
            const Icon = v === "board" ? LayoutGrid : List;
            return (
              <Button key={v} variant={view === v ? "secondary" : "ghost"} size="sm" aria-pressed={view === v} onClick={() => set({ view: v === "board" ? undefined : v })}>
                <Icon aria-hidden />{t(`view.${v}`)}
              </Button>
            );
          })}
        </div>
      </Card>

      {workflow.data && <WorkflowBanner wf={workflow.data} />}

      {tasks.isLoading && <div className="grid gap-3 sm:grid-cols-3" aria-busy="true">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-40" />)}</div>}
      {tasks.isError && <Card role="alert" className="p-6 text-center text-destructive">{tc("error")}</Card>}
      {tasks.data && tasks.data.length === 0 && (
        <Card className="flex flex-col items-center gap-3 p-10 text-center">
          <ClipboardList className="size-8 text-muted-foreground" aria-hidden />
          <p className="text-muted-foreground">{projects.data?.length ? t("empty") : t("emptyNoProject")}</p>
        </Card>
      )}

      {tasks.data && tasks.data.length > 0 && view === "board" && (
        <div className="overflow-x-auto pb-2 lg:overflow-visible" tabIndex={0} role="region" aria-label={t("view.board")} data-testid="task-board">
          {/* narrow screens: one scrolling row; wide screens: all nine columns visible, wrapping into rows */}
          <div className="grid min-w-max grid-flow-col auto-cols-[minmax(15rem,17rem)] gap-3 lg:min-w-0 lg:grid-flow-row lg:grid-cols-3 xl:grid-cols-5">
            {BOARD_COLUMNS.map((col) => {
              const items = byStatus.get(col) ?? [];
              return (
                <section key={col} aria-labelledby={`col-${col}`} className="flex flex-col gap-2 rounded-lg bg-muted/40 p-2" data-testid={`column-${col}`}>
                  <h2 id={`col-${col}`} className="flex items-center justify-between px-1 py-1 text-sm font-semibold">
                    <ColumnTitle status={col} />
                    <span className="tabular-nums text-muted-foreground" aria-label={t("count", { n: items.length })}>{items.length}</span>
                  </h2>
                  <ul className="flex flex-col gap-2">
                    {items.map((task) => (
                      <li key={task.id}>
                        <button type="button" onClick={() => set({ task: task.id })} data-testid={`task-card-${task.id}`}
                          className="flex min-h-11 w-full flex-col gap-1.5 rounded-md border bg-card p-3 text-start shadow-sm transition-colors hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                          <span className="text-sm font-medium leading-snug" dir="auto">{task.title}</span>
                          <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                            <span className="font-mono" dir="ltr">#{task.id}</span>
                            <span>{task.assigned_agent_id ? agentName.get(task.assigned_agent_id) ?? `#${task.assigned_agent_id}` : t("unassigned")}</span>
                            <span>{t("priorityShort", { n: task.priority })}</span>
                            {task.attempts > 1 && <span>{t("attemptsShort", { n: task.attempts })}</span>}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              );
            })}
          </div>
        </div>
      )}

      {tasks.data && tasks.data.length > 0 && view === "list" && (
        <Card className="overflow-x-auto" tabIndex={0} aria-label={t("view.list")}>
          <table className="w-full min-w-[760px] text-sm" data-testid="task-table">
            <thead className="border-b bg-muted/50 text-xs text-muted-foreground">
              <tr>{(["id", "title", "status", "agent", "priority", "attempts", "review"] as const).map((c) => (
                <th key={c} scope="col" className="px-4 py-3 text-start font-medium">{t(`columns.${c}`)}</th>
              ))}</tr>
            </thead>
            <tbody className="divide-y">
              {tasks.data.map((task) => (
                <tr key={task.id} className="hover:bg-muted/30">
                  <td className="px-4 py-3 font-mono text-xs" dir="ltr">#{task.id}</td>
                  <td className="px-4 py-3">
                    <button type="button" className="min-h-11 text-start font-medium hover:underline" onClick={() => set({ task: task.id })} dir="auto">{task.title}</button>
                  </td>
                  <td className="px-4 py-3"><TaskStatusBadge status={task.status} /></td>
                  <td className="px-4 py-3">{task.assigned_agent_id ? agentName.get(task.assigned_agent_id) ?? `#${task.assigned_agent_id}` : "—"}</td>
                  <td className="px-4 py-3 tabular-nums">{task.priority}</td>
                  <td className="px-4 py-3 tabular-nums">{task.attempts}/{task.max_attempts}</td>
                  <td className="px-4 py-3">{t(`reviewStatus.${task.review_status}`)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {taskId && <TaskDetails id={taskId} agentName={agentName} projects={projects.data ?? []} onClose={() => set({ task: undefined })} />}
      {dialog === "project" && <NewProjectDialog onClose={() => setDialog(null)} onCreated={(id) => set({ project: id, workflow: undefined })} />}
      {dialog === "objective" && (
        <NewObjectiveDialog projectId={projectId} onClose={() => setDialog(null)} onCreated={(pid, wid) => set({ project: pid, workflow: wid, task: undefined })} />
      )}
    </div>
  );
}

/** Column heading: full-contrast text; the status color only tints the icon (never color alone). */
function ColumnTitle({ status }: { status: string }) {
  const t = useTranslations("taskStatus");
  const Icon = statusIcon(status);
  return (
    <span className="inline-flex items-center gap-1.5">
      <Icon className={cn("size-4", statusTone(status))} aria-hidden />
      {t(status)}
    </span>
  );
}

function WorkflowBanner({ wf }: { wf: NonNullable<ReturnType<typeof useWorkflow>["data"]> }) {
  const t = useTranslations("tasks");
  const tone = wf.status === "failed" ? "border-destructive/40 text-destructive" : wf.status === "completed" ? "border-success/40" : "";
  const Icon = wf.status === "planning" || wf.status === "running" ? Loader2 : wf.status === "completed" ? CheckCircle2 : wf.status === "failed" ? XCircle : Target;
  const spin = wf.status === "planning" || wf.status === "running";
  return (
    <Card className={cn("flex flex-col gap-2 p-4", tone)} role="status" data-testid="workflow-banner">
      <div className="flex flex-wrap items-center gap-2">
        <Icon className={cn("size-4", spin && "motion-safe:animate-spin")} aria-hidden />
        <span className="font-semibold" dir="auto">#{wf.id} {wf.title}</span>
        <span className="text-sm">— {t(`workflowStatus.${wf.status}`)}</span>
        {wf.plan?.planner?.is_mock && (
          <span className="inline-flex items-center gap-1 rounded-full bg-warning/10 px-2 py-0.5 text-xs font-medium text-warning">
            <Sparkles className="size-3.5" aria-hidden />{t("mockPlan")}
          </span>
        )}
      </div>
      {wf.plan?.summary && <p className="text-sm text-muted-foreground" dir="auto">{wf.plan.summary}</p>}
      {wf.error && <p className="text-sm" dir="auto">{wf.error}</p>}
    </Card>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

function TaskDetails({ id, agentName, projects, onClose }: {
  id: number; agentName: Map<number, string>; projects: { id: number; name: string }[]; onClose: () => void;
}) {
  const t = useTranslations("tasks");
  const tc = useTranslations("common");
  const can = useCan();
  const task = useTask(id);
  const review = useReview();
  const toast = useToasts((s) => s.push);
  const errorText = useErrorText();
  const [note, setNote] = useState("");
  const [noteError, setNoteError] = useState(false);
  const d = task.data;
  const deps = useTasks({ workflowId: d?.workflow_id ?? undefined });
  const title = (tid: number) => deps.data?.find((x) => x.id === tid)?.title ?? `#${tid}`;

  function decide(decision: "approve" | "revise" | "reject") {
    if (decision !== "approve" && !note.trim()) {
      setNoteError(true);
      return;
    }
    review.mutate({ id, decision, note }, {
      onSuccess: () => { toast("success", t(`reviewDone.${decision}`)); setNote(""); setNoteError(false); },
      onError: (e) => toast("error", errorText(e)),
    });
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-h-[90vh] max-w-2xl overflow-y-auto" aria-describedby={undefined}>
        {!d && <div aria-busy="true" className="flex flex-col gap-3"><Skeleton className="h-6 w-2/3" /><Skeleton className="h-24" /></div>}
        {d && (
          <div className="flex flex-col gap-5" data-testid="task-details">
            <div className="flex flex-col gap-2 pe-8">
              <DialogTitle className="text-lg font-semibold leading-snug" dir="auto">{d.title}</DialogTitle>
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <TaskStatusBadge status={d.status} />
                <span className="font-mono" dir="ltr">#{d.id}</span>
                <span>{t(`reviewStatus.${d.review_status}`)}</span>
              </div>
            </div>
            {d.description && <p className="whitespace-pre-wrap text-sm" dir="auto">{d.description}</p>}

            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
              {([
                ["project", projects.find((p) => p.id === d.project_id)?.name ?? "—"],
                ["agent", d.assigned_agent_id ? agentName.get(d.assigned_agent_id) ?? `#${d.assigned_agent_id}` : t("unassigned")],
                ["priority", String(d.priority)],
                ["attempts", `${d.attempts}/${d.max_attempts}`],
                ["budget", d.budget_usd ? `$${d.budget_usd}` : "—"],
                ["effort", d.estimated_effort ?? "—"],
              ] as const).map(([k, v]) => (
                <div key={k} className="flex flex-col"><dt className="text-xs text-muted-foreground">{t(`fields.${k}`)}</dt><dd dir="auto">{v}</dd></div>
              ))}
            </dl>

            <Section title={t("fields.dependencies")}>
              {d.depends_on.length === 0 ? <p className="text-sm text-muted-foreground">{tc("none")}</p> : (
                <ul className="list-disc ps-5 text-sm">{d.depends_on.map((x) => <li key={x} dir="auto">{title(x)}</li>)}</ul>
              )}
            </Section>

            <Section title={t("fields.acceptance")}>
              <ul className="list-disc ps-5 text-sm" data-testid="acceptance-criteria">
                {d.acceptance_criteria.map((c, i) => <li key={i} dir="auto">{c}</li>)}
              </ul>
            </Section>

            {d.result?.output && (
              <Section title={t("fields.output")}>
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-3 text-xs" dir="auto">{d.result.output}</pre>
              </Section>
            )}
            {d.error && <p role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive" dir="auto">{d.error}</p>}

            <Section title={t("fields.history")}>
              {d.executions.length === 0 ? <p className="text-sm text-muted-foreground">{tc("none")}</p> : (
                <ol className="flex flex-col gap-1 text-sm">
                  {d.executions.map((e, i) => (
                    <li key={e.id} className="flex flex-wrap items-center gap-2">
                      <span className="tabular-nums text-muted-foreground">{i + 1}.</span>
                      <span className="font-mono text-xs" dir="ltr">{e.id.slice(0, 8)}</span>
                      <span>{e.status}</span>
                      {e.agent_id && <span className="text-muted-foreground">{agentName.get(e.agent_id) ?? `#${e.agent_id}`}</span>}
                      {e.is_mock && <span className="rounded bg-warning/10 px-1.5 text-xs text-warning">{t("mock")}</span>}
                    </li>
                  ))}
                </ol>
              )}
            </Section>

            <Section title={t("fields.evidence")}>
              {d.evidence.length === 0 ? <p className="text-sm text-muted-foreground">{t("noEvidence")}</p> : (
                <ul className="flex flex-col gap-1 text-sm" data-testid="evidence">
                  {d.evidence.map((ev, i) => (
                    <li key={i} className="rounded-md border p-2" dir="auto">
                      {ev.kind === "execution"
                        ? <>{t("evidenceExecution")} <span className="font-mono text-xs" dir="ltr">{ev.execution_id.slice(0, 8)} · sha256 {ev.output_sha256.slice(0, 12)}</span>{ev.is_mock && <> · {t("mock")}</>}</>
                        : <>{t(`reviewDone.${ev.decision}`)}{ev.note && <>: {ev.note}</>}</>}
                    </li>
                  ))}
                </ul>
              )}
            </Section>

            {d.status === "REVIEW" && can("tasks.control") && (
              <Section title={t("review.title")}>
                <p className="text-xs text-muted-foreground">{t("review.help")}</p>
                <Label htmlFor="review-note" className="mt-1">{t("review.note")}</Label>
                <Textarea id="review-note" value={note} rows={3} dir="auto" aria-invalid={noteError || undefined}
                  aria-describedby={noteError ? "review-note-error" : undefined} onChange={(e) => { setNote(e.target.value); setNoteError(false); }} />
                {noteError && <p id="review-note-error" className="text-sm text-destructive">{t("review.noteRequired")}</p>}
                <div className="mt-1 flex flex-wrap justify-end gap-2">
                  <Button variant="outline" disabled={review.isPending} onClick={() => decide("reject")}><XCircle aria-hidden />{t("review.reject")}</Button>
                  <Button variant="outline" disabled={review.isPending} onClick={() => decide("revise")}><RotateCcw aria-hidden />{t("review.revise")}</Button>
                  <Button disabled={review.isPending} onClick={() => decide("approve")} data-testid="review-approve"><CheckCircle2 aria-hidden />{t("review.approve")}</Button>
                </div>
              </Section>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function NewProjectDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (id: number) => void }) {
  const t = useTranslations("tasks");
  const tc = useTranslations("common");
  const agents = useAgents({});
  const create = useCreateProject();
  const errorText = useErrorText();
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<number[]>([]);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-h-[90vh] overflow-y-auto">
        <DialogTitle className="text-lg font-semibold">{t("newProject")}</DialogTitle>
        <DialogDescription className="text-sm text-muted-foreground">{t("projectHelp")}</DialogDescription>
        <form className="mt-4 flex flex-col gap-3" onSubmit={(e) => {
          e.preventDefault();
          setError(null);
          const fd = new FormData(e.currentTarget);
          create.mutate({ name: String(fd.get("name")).trim(), description: String(fd.get("description") ?? ""), agent_ids: picked }, {
            onSuccess: (p) => { onCreated(p.id); onClose(); },
            onError: (err) => setError(errorText(err)),
          });
        }}>
          {error && <p role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">{error}</p>}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="pr-name">{t("fields.name")}</Label>
            <Input id="pr-name" name="name" required maxLength={128} dir="auto" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="pr-desc">{t("fields.description")} ({tc("optional")})</Label>
            <Textarea id="pr-desc" name="description" rows={2} dir="auto" />
          </div>
          <fieldset className="flex flex-col gap-1.5">
            <legend className="mb-1 text-sm font-medium">{t("fields.team")}</legend>
            <p className="text-xs text-muted-foreground">{t("teamHelp")}</p>
            <div className="flex max-h-48 flex-col gap-1 overflow-y-auto rounded-md border p-2">
              {(agents.data ?? []).filter((a) => !a.archived_at).map((a) => (
                <label key={a.id} className="flex min-h-10 items-center gap-2 text-sm">
                  <input type="checkbox" className="size-4 accent-[var(--primary)]" checked={picked.includes(a.id)}
                    onChange={(e) => setPicked((p) => (e.target.checked ? [...p, a.id] : p.filter((x) => x !== a.id)))} />
                  <span dir="auto">{a.name}</span>
                  <span className="text-xs text-muted-foreground" dir="ltr">{a.role}</span>
                </label>
              ))}
            </div>
          </fieldset>
          <div className="mt-2 flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>{tc("cancel")}</Button>
            <Button type="submit" disabled={create.isPending} data-testid="project-submit">{tc("create")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function NewObjectiveDialog({ projectId, onClose, onCreated }: {
  projectId?: number; onClose: () => void; onCreated: (projectId: number, workflowId: number) => void;
}) {
  const t = useTranslations("tasks");
  const tc = useTranslations("common");
  const projects = useProjects();
  const submit = useSubmitObjective();
  const errorText = useErrorText();
  const [error, setError] = useState<string | null>(null);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-h-[90vh] overflow-y-auto">
        <DialogTitle className="text-lg font-semibold">{t("newObjective")}</DialogTitle>
        <DialogDescription className="text-sm text-muted-foreground">{t("objectiveHelp")}</DialogDescription>
        <form className="mt-4 flex flex-col gap-3" onSubmit={(e) => {
          e.preventDefault();
          setError(null);
          const fd = new FormData(e.currentTarget);
          const pid = Number(fd.get("project_id"));
          submit.mutate({ project_id: pid, title: String(fd.get("title")).trim(), objective: String(fd.get("objective")).trim() }, {
            onSuccess: (wf) => { onCreated(pid, wf.id); onClose(); },
            onError: (err) => setError(errorText(err)),
          });
        }}>
          {error && <p role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">{error}</p>}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ob-project">{t("filters.project")}</Label>
            <NativeSelect id="ob-project" name="project_id" defaultValue={projectId ?? projects.data?.[0]?.id} required>
              {projects.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </NativeSelect>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ob-title">{t("fields.objectiveTitle")}</Label>
            <Input id="ob-title" name="title" required maxLength={255} dir="auto" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ob-text">{t("fields.objective")}</Label>
            <Textarea id="ob-text" name="objective" required rows={6} maxLength={20000} dir="auto" aria-describedby="ob-text-help" />
            <p id="ob-text-help" className="text-xs text-muted-foreground">{t("objectiveTextHelp")}</p>
          </div>
          <div className="mt-2 flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>{tc("cancel")}</Button>
            <Button type="submit" disabled={submit.isPending} data-testid="objective-submit">{t("submitObjective")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
