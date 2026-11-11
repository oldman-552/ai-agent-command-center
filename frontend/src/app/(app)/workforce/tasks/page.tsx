"use client";
import { AlertTriangle, Search, ShieldCheck, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { LifecycleBadge, ModeBadge, PageHeader, useReason, WorkforceTabs } from "@/components/workforce/shared";
import { TaskDialog } from "@/components/workforce/task-dialog";
import { BOARD, LIFECYCLES, type TaskCard, useObjectives, useWfTasks, useWorkforce } from "@/lib/workforce";

export default function WorkforceTasksRoute() {
  return <Suspense><WorkforceTasksPage /></Suspense>;
}

const QUEUES = ["", "offered", "mine", "review", "open"] as const;

function WorkforceTasksPage() {
  const t = useTranslations("workforce.tasks");
  const tw = useTranslations("workforce");
  const tc = useTranslations("common");
  const sp = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const set = (patch: Record<string, string | undefined>) => {
    const next = new URLSearchParams(sp.toString());
    for (const [k, v] of Object.entries(patch)) (v ? next.set(k, v) : next.delete(k));
    router.replace(`${pathname}?${next}`, { scroll: false });
  };
  const num = (k: string) => (sp.get(k) ? Number(sp.get(k)) : undefined);
  const queue = (QUEUES as readonly string[]).includes(sp.get("queue") ?? "") ? (sp.get("queue") ?? "") : "";
  const filters = { queue: queue || undefined, objective: num("objective"), worker: num("worker"), mode: sp.get("mode") ?? undefined,
    lifecycle: sp.get("state") ?? undefined, q: sp.get("q") ?? undefined };
  const tasks = useWfTasks(filters);
  const objectives = useObjectives();
  const force = useWorkforce().data?.workforce;
  const [q, setQ] = useState(filters.q ?? "");
  const openId = num("task");
  const columns = useMemo(() => {
    const by = new Map<string, TaskCard[]>(BOARD.map((c) => [c.key, []]));
    for (const task of tasks.data?.tasks ?? []) by.get(BOARD.find((c) => c.states.includes(task.lifecycle))?.key ?? "waiting")?.push(task);
    return by;
  }, [tasks.data]);
  const filtered = !!(filters.queue || filters.objective || filters.worker || filters.mode || filters.lifecycle || filters.q);

  return (
    <div className="mx-auto flex max-w-[100rem] flex-col gap-6">
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <WorkforceTabs />

      <Card className="flex flex-col gap-4 p-4">
        <div role="group" aria-label={t("queue.label")} className="flex w-fit flex-wrap rounded-md border p-0.5" data-testid="wf-queue-filter">
          {QUEUES.map((k) => (
            <Button key={k || "all"} variant={queue === k ? "secondary" : "ghost"} size="sm" aria-pressed={queue === k}
              onClick={() => set({ queue: k || undefined })} data-testid={`wf-queue-${k || "all"}`}>
              {t(`queue.${k || "all"}`)}
            </Button>
          ))}
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wf-f-objective">{t("filters.objective")}</Label>
            <NativeSelect id="wf-f-objective" value={filters.objective ?? ""} onChange={(e) => set({ objective: e.target.value || undefined })}>
              <option value="">{tc("all")}</option>
              {objectives.data?.map((o) => <option key={o.id} value={o.id}>#{o.id} {o.title}</option>)}
            </NativeSelect>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wf-f-worker">{t("filters.worker")}</Label>
            <NativeSelect id="wf-f-worker" value={filters.worker ?? ""} onChange={(e) => set({ worker: e.target.value || undefined })}>
              <option value="">{tc("all")}</option>
              {force?.workers.map((w) => <option key={w.id} value={w.id}>{w.label} — {w.name}</option>)}
            </NativeSelect>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wf-f-mode">{t("filters.mode")}</Label>
            <NativeSelect id="wf-f-mode" value={filters.mode ?? ""} onChange={(e) => set({ mode: e.target.value || undefined })}>
              <option value="">{tc("all")}</option>
              <option value="AUTOMATIC">{tw("mode.AUTOMATIC")}</option>
              <option value="HUMAN_ASSISTED">{tw("mode.HUMAN_ASSISTED")}</option>
            </NativeSelect>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wf-f-state">{t("filters.state")}</Label>
            <NativeSelect id="wf-f-state" value={filters.lifecycle ?? ""} onChange={(e) => set({ state: e.target.value || undefined })}>
              <option value="">{tc("all")}</option>
              {LIFECYCLES.map((s) => <option key={s} value={s}>{tw(`lifecycle.${s}`)}</option>)}
            </NativeSelect>
          </div>
          <form className="flex flex-col gap-1.5" role="search" onSubmit={(e) => { e.preventDefault(); set({ q: q.trim() || undefined }); }}>
            <Label htmlFor="wf-f-q">{t("filters.search")}</Label>
            <div className="flex gap-2">
              <Input id="wf-f-q" type="search" value={q} onChange={(e) => setQ(e.target.value)} maxLength={100} dir="auto" />
              <Button type="submit" variant="outline" size="icon" aria-label={tc("search")}><Search aria-hidden /></Button>
            </div>
          </form>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
          <p role="status" data-testid="wf-task-count">{tasks.data ? t("count", { n: tasks.data.total }) : tc("loading")}</p>
          {filtered && (
            <Button variant="ghost" size="sm" onClick={() => { setQ(""); router.replace(pathname, { scroll: false }); }}>
              <X aria-hidden />{t("filters.clear")}
            </Button>
          )}
        </div>
      </Card>

      {tasks.isLoading && <div className="grid gap-3 md:grid-cols-3" aria-busy="true">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-40" />)}</div>}
      {tasks.isError && <Card role="alert" className="p-6 text-center text-destructive">{tc("error")}</Card>}
      {tasks.data && tasks.data.total === 0 && (
        <Card className="p-10 text-center text-muted-foreground" data-testid="wf-tasks-empty">{filtered ? t("emptyFiltered") : t("empty")}</Card>
      )}
      {tasks.data && tasks.data.total > 0 && (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6" data-testid="wf-board">
          {BOARD.map((col) => {
            const items = columns.get(col.key) ?? [];
            return (
              <section key={col.key} aria-labelledby={`wf-col-${col.key}`} className="flex min-w-0 flex-col gap-2 rounded-lg bg-muted/50 p-3" data-testid={`wf-col-${col.key}`}>
                <h2 id={`wf-col-${col.key}`} className="flex items-center justify-between text-sm font-semibold">
                  {t(`columns.${col.key}`)}<span className="rounded-full bg-muted px-2 text-xs tabular-nums">{items.length}</span>
                </h2>
                {items.length === 0 ? <p className="py-4 text-center text-xs text-muted-foreground">{t("columnEmpty")}</p> : (
                  <ul className="flex flex-col gap-2">
                    {items.map((task) => <li key={task.id}><BoardCard task={task} onOpen={() => set({ task: String(task.id) })} /></li>)}
                  </ul>
                )}
              </section>
            );
          })}
        </div>
      )}

      {openId && <TaskDialog id={openId} onClose={() => set({ task: undefined })} />}
    </div>
  );
}

function BoardCard({ task, onOpen }: { task: TaskCard; onOpen: () => void }) {
  const t = useTranslations("workforce.tasks");
  const reason = useReason();
  return (
    <button type="button" onClick={onOpen} data-testid={`wf-task-${task.id}`}
      className="flex w-full flex-col gap-2 rounded-lg border bg-card p-3 text-start shadow-sm transition-colors duration-200 hover:border-primary focus-visible:outline-2 focus-visible:outline-ring">
      <span className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-muted-foreground" dir="ltr">#{task.id}</span>
        <LifecycleBadge state={task.lifecycle} />
      </span>
      <span className="text-sm font-medium" dir="auto">{task.title}</span>
      <span className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {task.worker && <span><span className="font-mono" dir="ltr">{task.worker.label}</span> <span dir="auto">{task.worker.name}</span></span>}
        {task.mode && <ModeBadge mode={task.mode} />}
      </span>
      {task.needs_human_review && (
        <span className="flex items-center gap-1.5 text-xs font-medium"><AlertTriangle className="size-3.5 text-warning" aria-hidden />{t("needsPerson")}</span>
      )}
      {task.waiting && <span className="text-xs text-muted-foreground">{t("waiting", { reason: reason(task.waiting.reason) })}</span>}
      {task.fallback && <span className="text-xs text-muted-foreground">{t("fallback", { reason: reason(task.fallback.reason) })}</span>}
      {task.critical_operation && (
        <span className="flex items-center gap-1.5 text-xs"><ShieldCheck className="size-3.5 text-warning" aria-hidden />{t("critical")}</span>
      )}
    </button>
  );
}
