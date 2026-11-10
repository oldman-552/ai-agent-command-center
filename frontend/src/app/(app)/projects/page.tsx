"use client";
import { FolderGit2, GitPullRequestArrow, Plus, ScanSearch } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import { useState } from "react";
import { useCan } from "@/components/providers";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { type ProjectFull, useAnalysis, useProjectActions, useProjectsFull, useTeams } from "@/lib/admin";
import { useAgents } from "@/lib/agents";
import { ApiError } from "@/lib/api";
import { useToasts } from "@/lib/toast";

const MODES = ["new", "existing", "maintenance"] as const;

function useFail() {
  const tc = useTranslations("common");
  const t = useTranslations("projects");
  const toast = useToasts((s) => s.push);
  return (e: unknown) => {
    const code = e instanceof ApiError ? e.code : "error";
    toast("error", t.has(`errors.${code}`) ? t(`errors.${code}`) : tc("requestFailed", { code }));
  };
}

export default function ProjectsPage() {
  const t = useTranslations("projects");
  const can = useCan();
  const projects = useProjectsFull();
  const teams = useTeams(can("teams.read"));
  const [editing, setEditing] = useState<ProjectFull | "new">();
  const [open, setOpen] = useState<ProjectFull>();
  const teamName = (id: number | null) => teams.data?.find((x) => x.id === id)?.name;
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{t("title")}</h1>
          <p className="max-w-3xl text-sm text-muted-foreground">{t("subtitle")}</p>
        </div>
        {can("projects.write") && <Button onClick={() => setEditing("new")} data-testid="project-new"><Plus aria-hidden />{t("new")}</Button>}
      </div>
      {projects.isLoading && <Skeleton className="h-40" aria-busy="true" />}
      {projects.data?.length === 0 && <Card className="p-8 text-center text-muted-foreground">{t("empty")}</Card>}
      <ul className="grid gap-3 md:grid-cols-2" aria-label={t("title")}>
        {projects.data?.map((p) => (
          <li key={p.id}>
            <Card className="flex h-full flex-col gap-2 p-4" data-testid={`project-${p.id}`}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <h2 className="text-base font-semibold" dir="auto">{p.name}</h2>
                <span className="rounded-full border px-2 py-0.5 text-xs font-medium">{t(`modes.${p.mode}`)}</span>
              </div>
              {p.description && <p className="line-clamp-2 text-sm text-muted-foreground" dir="auto">{p.description}</p>}
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                <dt className="text-muted-foreground">{t("agents")}</dt><dd>{p.agent_ids.length}</dd>
                <dt className="text-muted-foreground">{t("team")}</dt><dd dir="auto">{teamName(p.team_id) ?? "—"}</dd>
                <dt className="text-muted-foreground">{t("budget")}</dt><dd className="tabular-nums">{p.budget_usd ? `$${p.budget_usd}` : "—"}</dd>
                <dt className="text-muted-foreground">{t("repository")}</dt><dd>{p.repository_id ? t("linked") : "—"}</dd>
              </dl>
              <div className="mt-auto flex flex-wrap gap-2 pt-2">
                <Button size="sm" variant="outline" onClick={() => setOpen(p)} aria-label={`${t("repoMap")}: ${p.name}`}><ScanSearch aria-hidden />{t("repoMap")}</Button>
                {can("projects.write") && <Button size="sm" variant="outline" onClick={() => setEditing(p)} aria-label={`${t("edit")}: ${p.name}`}>{t("edit")}</Button>}
              </div>
            </Card>
          </li>
        ))}
      </ul>
      {editing && <ProjectDialog project={editing === "new" ? undefined : editing} onClose={() => setEditing(undefined)} />}
      {open && <AnalysisDialog project={open} onClose={() => setOpen(undefined)} />}
    </div>
  );
}

function ProjectDialog({ project, onClose }: { project?: ProjectFull; onClose: () => void }) {
  const t = useTranslations("projects");
  const tc = useTranslations("common");
  const can = useCan();
  const agents = useAgents({});
  const teams = useTeams(can("teams.read"));
  const a = useProjectActions();
  const fail = useFail();
  const [name, setName] = useState(project?.name ?? "");
  const [description, setDescription] = useState(project?.description ?? "");
  const [mode, setMode] = useState<string>(project?.mode ?? "new");
  const [team, setTeam] = useState<string>(project?.team_id ? String(project.team_id) : "");
  const [budget, setBudget] = useState(project?.budget_usd ?? "");
  const [ids, setIds] = useState<number[]>(project?.agent_ids ?? []);
  const busy = a.create.isPending || a.update.isPending;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-h-[90vh] max-w-xl overflow-y-auto">
        <DialogTitle>{project ? t("editTitle", { name: project.name }) : t("new")}</DialogTitle>
        <DialogDescription>{t("modeHelp")}</DialogDescription>
        <form className="mt-4 flex flex-col gap-4" onSubmit={(e) => {
          e.preventDefault();
          if (project) {
            a.update.mutate({ id: project.id, description, mode, team_id: team ? Number(team) : null, agent_ids: ids,
              ...(can("budgets.manage") ? { budget_usd: budget.trim() || null } : {}) }, { onSuccess: onClose, onError: fail });
          } else {
            a.create.mutate({ name: name.trim(), description, mode, agent_ids: ids }, { onSuccess: onClose, onError: fail });
          }
        }}>
          {!project && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="p-name">{t("name")}</Label>
              <Input id="p-name" required maxLength={128} value={name} onChange={(e) => setName(e.target.value)} dir="auto" />
            </div>
          )}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="p-desc">{t("description")}</Label>
            <Textarea id="p-desc" rows={3} maxLength={8000} value={description} onChange={(e) => setDescription(e.target.value)} dir="auto" />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="p-mode">{t("mode")}</Label>
              <NativeSelect id="p-mode" value={mode} onChange={(e) => setMode(e.target.value)}>
                {MODES.map((m) => <option key={m} value={m}>{t(`modes.${m}`)}</option>)}
              </NativeSelect>
            </div>
            {project && can("teams.read") && (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="p-team">{t("team")}</Label>
                <NativeSelect id="p-team" value={team} onChange={(e) => setTeam(e.target.value)}>
                  <option value="">{tc("none")}</option>
                  {teams.data?.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}
                </NativeSelect>
              </div>
            )}
          </div>
          {project && can("budgets.manage") && (
            <div className="flex max-w-xs flex-col gap-1.5">
              <Label htmlFor="p-budget">{t("budgetUsd")}</Label>
              <Input id="p-budget" inputMode="decimal" pattern="\d{0,8}(\.\d{0,4})?" value={budget} onChange={(e) => setBudget(e.target.value)} dir="ltr" aria-describedby="p-budget-help" />
              <p id="p-budget-help" className="text-xs text-muted-foreground">{t("budgetHelp")}</p>
            </div>
          )}
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-sm font-medium">{t("agents")}</legend>
            <div className="grid gap-2 sm:grid-cols-2">
              {agents.data?.map((ag) => (
                <label key={ag.id} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" className="size-4" checked={ids.includes(ag.id)} onChange={(e) => setIds(e.target.checked ? [...ids, ag.id] : ids.filter((x) => x !== ag.id))} />
                  <span dir="auto">{ag.name}</span>
                </label>
              ))}
            </div>
          </fieldset>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>{tc("cancel")}</Button>
            <Button type="submit" disabled={busy} data-testid="project-save">{busy ? tc("saving") : tc("save")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function AnalysisDialog({ project, onClose }: { project: ProjectFull; onClose: () => void }) {
  const t = useTranslations("projects");
  const tc = useTranslations("common");
  const fmt = useFormatter();
  const can = useCan();
  const analysis = useAnalysis(project.id, !!project.analyzed_at);
  const a = useProjectActions();
  const fail = useFail();
  const toast = useToasts((s) => s.push);
  const d = a.analyze.data ?? analysis.data;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogTitle className="flex items-center gap-2"><FolderGit2 className="size-5" aria-hidden /><span dir="auto">{project.name}</span></DialogTitle>
        <DialogDescription>{t("repoMapHelp")}</DialogDescription>
        <div className="mt-4 flex flex-wrap gap-2">
          {can("github.manage") && project.repository_id && (
            <Button size="sm" variant="outline" disabled={a.importRepo.isPending} onClick={() => a.importRepo.mutate(project.id, { onSuccess: () => toast("success", t("imported")), onError: fail })}>
              <GitPullRequestArrow aria-hidden />{t("import")}
            </Button>
          )}
          {can("projects.write") && (
            <Button size="sm" disabled={a.analyze.isPending} onClick={() => a.analyze.mutate(project.id, { onError: fail })} data-testid="project-analyze">
              <ScanSearch aria-hidden />{a.analyze.isPending ? t("analyzing") : t("analyze")}
            </Button>
          )}
        </div>
        {!d && <p className="mt-4 text-sm text-muted-foreground">{t("notAnalyzed")}</p>}
        {d && (
          <div className="mt-4 flex flex-col gap-4 text-sm" data-testid="project-analysis">
            <p className="text-xs text-muted-foreground">{t("analyzedAt", { at: fmt.dateTime(new Date(d.analyzed_at), { dateStyle: "medium", timeStyle: "short" }), files: d.files })}</p>
            <section aria-labelledby="an-tech"><h3 id="an-tech" className="font-semibold">{t("technologies")}</h3>
              <p dir="ltr" className="text-start">{d.technologies.languages.map((l) => `${l.name} (${l.files})`).join(", ") || "—"}</p>
              <p dir="ltr" className="text-start text-muted-foreground">{[...d.technologies.frameworks, ...d.technologies.infrastructure].join(", ") || "—"}</p>
            </section>
            <section aria-labelledby="an-arch"><h3 id="an-arch" className="font-semibold">{t("architecture")}</h3>
              <ul className="grid gap-1 sm:grid-cols-2">{d.architecture.slice(0, 16).map((x) => <li key={x.dir}><span className="font-mono" dir="ltr">{x.dir}/</span> — {t.has(`roles.${x.role}`) ? t(`roles.${x.role}`) : x.role} ({x.files})</li>)}</ul>
            </section>
            <section aria-labelledby="an-tests"><h3 id="an-tests" className="font-semibold">{t("tests")}</h3>
              <p>{t("testFiles", { n: d.tests.files })} {d.tests.frameworks.length > 0 && <span dir="ltr">({d.tests.frameworks.join(", ")})</span>}</p>
            </section>
            <section aria-labelledby="an-deps"><h3 id="an-deps" className="font-semibold">{t("dependencies")}</h3>
              <p>{t("depCount", { py: Object.keys(d.dependencies.python).length, node: Object.keys(d.dependencies.node).length, go: Object.keys(d.dependencies.go).length })}</p>
            </section>
            <section aria-labelledby="an-debt"><h3 id="an-debt" className="font-semibold">{t("debt")}</h3>
              {d.debt.length ? <ul className="list-inside list-disc">{d.debt.map((x) => <li key={x.kind}><span dir="ltr">{x.detail}</span></li>)}</ul> : <p className="text-muted-foreground">—</p>}
            </section>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
