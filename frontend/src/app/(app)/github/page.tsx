"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, CircleDot, Clock, ExternalLink, GitBranch, GitMerge, GitPullRequest, Lock, Plus, XCircle } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import { useState } from "react";
import { useCan } from "@/components/providers";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

type Repo = { id: number; full_name: string; default_branch: string; protected_branches: string[]; token_hint: string | null;
  webhook_configured: boolean; is_private: boolean };
type Pull = { id: number; repository_id: number; task_id: number | null; number: number; url: string; title: string;
  head_branch: string; base_branch: string; state: string; checks: Record<string, { status: string; conclusion: string | null }> | null };
type Wt = { id: number; task_id: number | null; branch: string; base_branch: string; head_commit: string | null; status: string;
  modified_files: string[]; conflicts: { files: string[]; with?: string; task_id?: number }[]; updated_at: string };

const fetchRepos = () => api<Repo[]>("/api/repositories");
const fetchPulls = () => api<Pull[]>("/api/pull-requests");
const fetchWorktrees = () => api<Wt[]>("/api/worktrees");

const WT_STATUS = { active: CircleDot, committed: GitBranch, merged: GitMerge, abandoned: XCircle } as const;

function CheckIcon({ conclusion }: { conclusion: string | null }) {
  if (conclusion === "success") return <CheckCircle2 className="size-4 text-success" aria-hidden />;
  if (conclusion === "failure" || conclusion === "error") return <XCircle className="size-4 text-destructive" aria-hidden />;
  return <Clock className="size-4 text-muted-foreground" aria-hidden />;
}

export default function GitHubPage() {
  const t = useTranslations("github");
  const tc = useTranslations("common");
  const fmt = useFormatter();
  const can = useCan();
  const repos = useQuery({ queryKey: ["repos"], queryFn: fetchRepos });
  const pulls = useQuery({ queryKey: ["pulls"], queryFn: fetchPulls, refetchInterval: 15_000 });
  const wts = useQuery({ queryKey: ["worktrees"], queryFn: fetchWorktrees, refetchInterval: 15_000 });
  const [open, setOpen] = useState(false);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{t("title")}</h1>
          <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
        </div>
        {can("credentials.manage") && <Button onClick={() => setOpen(true)} data-testid="connect-repo"><Plus aria-hidden />{t("connect")}</Button>}
      </div>

      <section aria-labelledby="h-repos" className="flex flex-col gap-3">
        <h2 id="h-repos" className="text-lg font-semibold">{t("repositories")}</h2>
        {repos.isLoading && <Skeleton className="h-24" aria-busy="true" />}
        {repos.isError && <Card role="alert" className="p-4 text-destructive">{tc("error")}</Card>}
        {repos.data?.length === 0 && <Card className="p-6 text-center text-muted-foreground">{t("noRepos")}</Card>}
        <ul className="grid gap-3 md:grid-cols-2" data-testid="repo-list">
          {repos.data?.map((r) => (
            <li key={r.id}>
              <Card className="flex flex-col gap-2 p-4">
                <span className="font-mono font-semibold" dir="ltr">{r.full_name}</span>
                <span className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                  <span className="inline-flex items-center gap-1"><GitBranch className="size-3.5" aria-hidden />{t("defaultBranch")}: <span dir="ltr">{r.default_branch}</span></span>
                  <span>{t("token")}: <span dir="ltr">{r.token_hint ?? "—"}</span></span>
                  <span>{r.webhook_configured ? t("webhookOn") : t("webhookOff")}</span>
                </span>
                <span className="flex flex-wrap items-center gap-1.5 text-xs">
                  <Lock className="size-3.5 text-warning" aria-hidden /><span>{t("protected")}:</span>
                  {r.protected_branches.map((b) => <span key={b} className="rounded bg-muted px-1.5 font-mono" dir="ltr">{b}</span>)}
                </span>
              </Card>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="h-pulls" className="flex flex-col gap-3">
        <h2 id="h-pulls" className="text-lg font-semibold">{t("pullRequests")}</h2>
        {pulls.data?.length === 0 && <Card className="p-6 text-center text-muted-foreground">{t("noPulls")}</Card>}
        {pulls.data && pulls.data.length > 0 && (
          <Card className="overflow-x-auto" tabIndex={0} aria-label={t("pullRequests")}>
            <table className="w-full min-w-[720px] text-sm" data-testid="pull-table">
              <thead className="border-b bg-muted/50 text-xs text-muted-foreground">
                <tr>{(["pr", "branch", "state", "checks"] as const).map((c) => <th key={c} scope="col" className="px-4 py-3 text-start font-medium">{t(`columns.${c}`)}</th>)}</tr>
              </thead>
              <tbody className="divide-y">
                {pulls.data.map((p) => (
                  <tr key={p.id}>
                    <td className="px-4 py-2">
                      <a href={p.url} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-10 items-center gap-1.5 text-primary hover:underline">
                        <GitPullRequest className="size-4" aria-hidden /><span dir="auto">#{p.number} {p.title}</span><ExternalLink className="size-3.5" aria-label={t("opensNewTab")} role="img" />
                      </a>
                    </td>
                    <td className="px-4 py-2 font-mono text-xs" dir="ltr">{p.head_branch} → {p.base_branch}</td>
                    <td className="px-4 py-2">{t.has(`pullState.${p.state}`) ? t(`pullState.${p.state}`) : p.state}</td>
                    <td className="px-4 py-2">
                      <ul className="flex flex-col gap-1">
                        {Object.entries(p.checks ?? {}).map(([name, c]) => (
                          <li key={name} className="flex items-center gap-1.5 text-xs">
                            <CheckIcon conclusion={c.conclusion} /><span dir="ltr">{name}</span>
                            <span className="text-muted-foreground">{c.conclusion ?? c.status}</span>
                          </li>
                        ))}
                        {!p.checks && <li className="text-xs text-muted-foreground">—</li>}
                      </ul>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}
      </section>

      <section aria-labelledby="h-wt" className="flex flex-col gap-3">
        <h2 id="h-wt" className="text-lg font-semibold">{t("worktrees")}</h2>
        <p className="text-sm text-muted-foreground">{t("worktreesHelp")}</p>
        {wts.data?.length === 0 && <Card className="p-6 text-center text-muted-foreground">{t("noWorktrees")}</Card>}
        <ul className="flex flex-col gap-2" data-testid="worktree-list">
          {wts.data?.map((w) => {
            const Icon = WT_STATUS[w.status as keyof typeof WT_STATUS] ?? CircleDot;
            return (
              <li key={w.id}>
                <Card className="flex flex-col gap-1.5 p-3 text-sm">
                  <span className="flex flex-wrap items-center gap-2">
                    <Icon className="size-4 text-primary" aria-hidden />
                    <span className="font-medium">{t(`wtStatus.${w.status}`)}</span>
                    <span className="font-mono text-xs" dir="ltr">{w.branch}</span>
                    {w.task_id && <a className="text-primary underline-offset-2 hover:underline" href={`/tasks?task=${w.task_id}`}>{t("task", { id: w.task_id })}</a>}
                    <span className="ms-auto text-xs text-muted-foreground">{fmt.relativeTime(new Date(w.updated_at))}</span>
                  </span>
                  <span className="text-xs text-muted-foreground">{t("files", { n: w.modified_files.length })}</span>
                  {w.conflicts.length > 0 && (
                    <span className="flex items-start gap-1.5 text-xs">
                      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
                      <span>{t("conflicts")}: <span dir="ltr">{w.conflicts.flatMap((c) => c.files).join(", ")}</span></span>
                    </span>
                  )}
                </Card>
              </li>
            );
          })}
        </ul>
      </section>

      {open && <ConnectDialog onClose={() => setOpen(false)} />}
    </div>
  );
}

function ConnectDialog({ onClose }: { onClose: () => void }) {
  const t = useTranslations("github");
  const tc = useTranslations("common");
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const connect = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<Repo>("/api/repositories", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["repos"] }); onClose(); },
    onError: (e) => {
      const code = e instanceof ApiError ? e.code : "network";
      const kind = code.startsWith("github:401") || code.startsWith("github:403") ? "badToken"
        : code.startsWith("github:404") ? "notFound" : code.startsWith("github:") ? "unreachable"
          : code === "repository_exists" ? "exists" : null;
      setError(kind ? t(`errors.${kind}`) : tc("requestFailed", { code }));
    },
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-h-[90vh] overflow-y-auto">
        <DialogTitle className="text-lg font-semibold">{t("connect")}</DialogTitle>
        <DialogDescription className="text-sm text-muted-foreground">{t("connectHelp")}</DialogDescription>
        <form className="mt-4 flex flex-col gap-3" onSubmit={(e) => {
          e.preventDefault();
          setError(null);
          const fd = new FormData(e.currentTarget);
          const prot = String(fd.get("protected") || "").split(",").map((s) => s.trim()).filter(Boolean);
          const secret = String(fd.get("webhook") || "").trim();
          connect.mutate({ full_name: String(fd.get("full_name")).trim(), token: String(fd.get("token")),
            ...(prot.length ? { protected_branches: prot } : {}), ...(secret ? { webhook_secret: secret } : {}) });
        }}>
          {error && <p role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">{error}</p>}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="gh-name">{t("fields.fullName")}</Label>
            <Input id="gh-name" name="full_name" required pattern="[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+" placeholder={t("fields.fullNamePlaceholder")} dir="ltr" aria-describedby="gh-name-help" />
            <p id="gh-name-help" className="text-xs text-muted-foreground">{t("fields.fullNameHelp")}</p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="gh-token">{t("fields.token")}</Label>
            <Input id="gh-token" name="token" type="password" required minLength={10} autoComplete="off" dir="ltr" aria-describedby="gh-token-help" />
            <p id="gh-token-help" className="text-xs text-muted-foreground">{t("fields.tokenHelp")}</p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="gh-prot">{t("fields.protected")} ({tc("optional")})</Label>
            <Input id="gh-prot" name="protected" placeholder={t("fields.protectedPlaceholder")} dir="ltr" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="gh-hook">{t("fields.webhook")} ({tc("optional")})</Label>
            <Input id="gh-hook" name="webhook" type="password" minLength={16} autoComplete="off" dir="ltr" aria-describedby="gh-hook-help" />
            <p id="gh-hook-help" className="text-xs text-muted-foreground">{t("fields.webhookHelp")}</p>
          </div>
          <div className="mt-2 flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>{tc("cancel")}</Button>
            <Button type="submit" disabled={connect.isPending} data-testid="connect-submit">{t("connectSubmit")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
