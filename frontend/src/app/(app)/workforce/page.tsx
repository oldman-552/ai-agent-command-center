"use client";
import { AlertTriangle, ArrowLeft, ArrowRight, Cable, Eye, Inbox, Info, Send, ShieldCheck, Target, UserCheck } from "lucide-react";
import { useFormatter, useLocale, useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useCan } from "@/components/providers";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { accountTag, MockBadge, ModeBadge, ObjectiveStatus, PageHeader, useActor, useFail, useReason, WorkforceTabs } from "@/components/workforce/shared";
import { useToasts } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { type Dashboard, type Objective, useWfDashboard, useWorkforceActions, type Worker } from "@/lib/workforce";

export default function WorkforceOverviewPage() {
  const t = useTranslations("workforce");
  const tc = useTranslations("common");
  const can = useCan();
  const dash = useWfDashboard();
  const a = useWorkforceActions();
  const fail = useFail();
  const d = dash.data;
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <WorkforceTabs />
      {dash.isLoading && <div className="flex flex-col gap-3" aria-busy="true"><Skeleton className="h-40" /><Skeleton className="h-40" /></div>}
      {dash.isError && <Card role="alert" className="p-6 text-center text-destructive">{tc("error")}</Card>}
      {d && !d.workforce && (
        <Card className="flex flex-col items-start gap-3 p-6" data-testid="wf-not-set-up">
          <p className="font-medium">{t("notSetUp")}</p>
          <p className="text-sm text-muted-foreground">{t("notSetUpHelp")}</p>
          {can("workforce.manage") && (
            <Button disabled={a.setup.isPending} onClick={() => a.setup.mutate(undefined, { onError: fail })}>{t("setup")}</Button>
          )}
        </Card>
      )}
      {d?.workforce && (
        <>
          {can("workforce.run") && <ObjectiveForm d={d} />}
          <Queues d={d} />
          <Team d={d} />
          <Bridges d={d} />
          <Objectives d={d} />
          <Recent d={d} />
        </>
      )}
    </div>
  );
}

function ObjectiveForm({ d }: { d: Dashboard }) {
  const t = useTranslations("workforce.overview");
  const tw = useTranslations("workforce");
  const tc = useTranslations("common");
  const a = useWorkforceActions();
  const fail = useFail();
  const router = useRouter();
  const toast = useToasts((s) => s.push);
  const reason = useReason();
  const force = d.workforce!;
  const [prompt, setPrompt] = useState("");
  const [title, setTitle] = useState("");
  const [policy, setPolicy] = useState<"manager" | "human">(force.review_policy);
  const ready = force.manager.configured;
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!prompt.trim() || !ready) return;
    a.submitObjective.mutate({ prompt, title: title.trim() || undefined, review_policy: policy }, {
      onError: fail,
      onSuccess: (o) => { toast("success", t("submitted")); router.push(`/workforce/objectives/${o.id}`); },
    });
  };
  return (
    <section aria-labelledby="wf-new-h">
      <Card className="flex flex-col gap-4 p-5">
        <div className="flex flex-wrap items-center gap-2">
          <Target className="size-5 text-primary" aria-hidden />
          <h2 id="wf-new-h" className="text-lg font-semibold">{t("newTitle")}</h2>
        </div>
        {!ready && (
          <div role="note" className="flex gap-3 rounded-md border border-warning p-3 text-sm" data-testid="wf-manager-missing">
            <AlertTriangle className="mt-0.5 size-5 shrink-0 text-warning" aria-hidden />
            <div className="flex flex-col gap-1">
              <p className="font-medium">{t("managerMissingTitle")}</p>
              <p>{t("managerMissingBody", { problem: reason(force.manager.problem) })}</p>
              <Link href="/workforce/workers" className="w-fit font-medium text-primary underline underline-offset-4">{t("managerMissingLink")}</Link>
            </div>
          </div>
        )}
        {ready && force.manager.is_mock && (
          <div role="note" className="flex gap-3 rounded-md border border-warning p-3 text-sm" data-testid="wf-manager-mock">
            <AlertTriangle className="mt-0.5 size-5 shrink-0 text-warning" aria-hidden />
            <p>{t("managerMock")}</p>
          </div>
        )}
        <form onSubmit={submit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wf-prompt">{t("prompt")}</Label>
            <Textarea id="wf-prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} required maxLength={20000}
              className="min-h-32" dir="auto" aria-describedby="wf-prompt-help" data-testid="wf-prompt" />
            <p id="wf-prompt-help" className="text-xs text-muted-foreground">{t("promptHelp")}</p>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="wf-title">{t("titleField")} <span className="font-normal text-muted-foreground">({tc("optional")})</span></Label>
              <Input id="wf-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={255} dir="auto" />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="wf-policy">{t("policy")}</Label>
              <NativeSelect id="wf-policy" value={policy} onChange={(e) => setPolicy(e.target.value as "manager" | "human")}>
                <option value="manager">{tw("policy.manager")}</option>
                <option value="human">{tw("policy.human")}</option>
              </NativeSelect>
            </div>
          </div>
          <div className="flex justify-end">
            <Button type="submit" disabled={!ready || !prompt.trim() || a.submitObjective.isPending} data-testid="wf-submit-objective">
              <Send aria-hidden />{a.submitObjective.isPending ? t("submitting") : t("submit")}
            </Button>
          </div>
        </form>
      </Card>
    </section>
  );
}

function Queues({ d }: { d: Dashboard }) {
  const t = useTranslations("workforce.overview");
  const items = [
    { key: "offered", n: d.queues.offered_to_me, href: "/workforce/tasks?queue=offered", icon: Inbox },
    { key: "mine", n: d.queues.accepted_by_me, href: "/workforce/tasks?queue=mine", icon: UserCheck },
    { key: "review", n: d.queues.needs_human_review, href: "/workforce/tasks?queue=review", icon: Eye },
    { key: "approvals", n: d.queues.pending_approvals, href: "/approvals", icon: ShieldCheck },
  ] as const;
  return (
    <section aria-labelledby="wf-queues-h" className="flex flex-col gap-3">
      <h2 id="wf-queues-h" className="text-lg font-semibold">{t("queues")}</h2>
      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="wf-queues">
        {items.map(({ key, n, href, icon: Icon }) => (
          <li key={key}>
            <Link href={href} data-testid={`wf-queue-${key}`}
              className="flex h-full flex-col gap-1 rounded-lg border bg-card p-4 shadow-sm transition-colors duration-200 hover:border-primary focus-visible:outline-2 focus-visible:outline-ring">
              <span className="flex items-center gap-2 text-sm text-muted-foreground"><Icon className="size-4" aria-hidden />{t(`queue.${key}`)}</span>
              <span className="text-2xl font-bold tabular-nums">{n}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

function WorkerSummary({ w }: { w: Worker }) {
  const t = useTranslations("workforce");
  const reason = useReason();
  const b = w.account_binding;
  return (
    <Card className="flex h-full flex-col gap-2 p-4" data-testid={`wf-worker-${w.slot}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="font-semibold" dir="auto"><span className="font-mono text-primary" dir="ltr">{w.label}</span> {w.name}</h3>
        <ModeBadge mode={w.mode} />
      </div>
      <p className="text-xs text-muted-foreground">{t("workers.role")}: <span dir="auto">{w.role}</span></p>
      <p className="text-sm" data-testid="wf-worker-status">
        {t(`workerStatus.${w.status.state}`)}{w.status.reason ? ` — ${reason(w.status.reason)}` : ""}
      </p>
      {w.mode === "AUTOMATIC" && (
        <p className="text-xs text-muted-foreground">
          {t(`binding.${b.type}`)}{b.account ? <> · <span dir="ltr" className="font-mono">{accountTag(b.account, b.account_hash)}</span></> : null}
        </p>
      )}
      {w.provider?.is_mock && <MockBadge short className="w-fit" />}
      <p className="mt-auto text-xs text-muted-foreground">{t("workers.busy", { n: w.busy_tasks })}</p>
    </Card>
  );
}

function Team({ d }: { d: Dashboard }) {
  const t = useTranslations("workforce");
  const force = d.workforce!;
  const m = force.manager;
  return (
    <section aria-labelledby="wf-team-h" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="wf-team-h" className="text-lg font-semibold">{t("overview.team")}</h2>
        <Link href="/workforce/workers" className="text-sm font-medium text-primary underline underline-offset-4">{t("overview.configure")}</Link>
      </div>
      <Card className="flex flex-wrap items-center gap-x-6 gap-y-2 p-4" data-testid="wf-manager">
        <h3 className="font-semibold">{t("manager.title")}</h3>
        {m.model ? (
          <p className="text-sm"><span dir="auto">{m.model.provider}</span> · <span className="font-mono" dir="ltr">{m.model.model}</span></p>
        ) : <p className="text-sm text-muted-foreground">{t("manager.noModel")}</p>}
        {m.is_mock && <MockBadge />}
        <p className="text-sm text-muted-foreground">{t(`policy.${force.review_policy}`)}</p>
      </Card>
      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {force.workers.map((w) => <li key={w.id}><WorkerSummary w={w} /></li>)}
      </ul>
      {force.accounts.automatic_workers > 0 && (
        <p className="text-sm text-muted-foreground" data-testid="wf-accounts">
          {t("overview.accounts", { auto: force.accounts.automatic_workers, distinct: force.accounts.distinct_accounts, pooled: force.accounts.pooled + force.accounts.unknown })}
          {" "}{t("binding.notAttested")}
        </p>
      )}
    </section>
  );
}

function Bridges({ d }: { d: Dashboard }) {
  const t = useTranslations("workforce");
  return (
    <section aria-labelledby="wf-bridges-h" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="wf-bridges-h" className="text-lg font-semibold">{t("bridge.title")}</h2>
        <Link href="/workforce/bridge" className="text-sm font-medium text-primary underline underline-offset-4">{t("overview.bridgeSettings")}</Link>
      </div>
      {d.bridges.length === 0 ? (
        <Card className="flex gap-3 p-4 text-sm" data-testid="wf-no-bridge">
          <Info className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden /><p>{t("overview.noBridge")}</p>
        </Card>
      ) : (
        <ul className="grid gap-3 md:grid-cols-2">
          {d.bridges.map((b) => {
            const state = !b.enabled ? "off" : !b.terms_acknowledged ? "terms" : !b.health_fresh ? "unknown" : b.usable ? "works" : "down";
            return (
              <li key={b.id}>
                <Card className="flex h-full flex-col gap-1 p-4" data-testid={`wf-bridge-${b.id}`}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h3 className="flex items-center gap-2 font-semibold" dir="auto"><Cable className="size-4 text-muted-foreground" aria-hidden />{b.name}</h3>
                    {b.is_mock && <MockBadge short />}
                  </div>
                  <p className="text-sm">{t(`bridge.state.${state}`)}</p>
                  <p className="text-xs text-muted-foreground">
                    {t("bridge.accountsReady", { n: b.accounts })}
                    {b.queue ? ` · ${t("bridge.queue", { active: b.queue.active, depth: b.queue.depth, max: b.queue.max_depth })}` : ""}
                  </p>
                </Card>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function ObjectiveRow({ o }: { o: Objective }) {
  const t = useTranslations("workforce");
  const fmt = useFormatter();
  const Arrow = useLocale() === "ar" ? ArrowLeft : ArrowRight;
  return (
    <Link href={`/workforce/objectives/${o.id}`} data-testid={`wf-objective-${o.id}`}
      className="flex flex-col gap-2 rounded-lg border bg-card p-4 shadow-sm transition-colors duration-200 hover:border-primary focus-visible:outline-2 focus-visible:outline-ring">
      <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <ObjectiveStatus status={o.status} />
        {(o.manager?.is_mock || o.report?.is_mock) && <MockBadge short />}
        <span className="font-mono text-xs text-muted-foreground" dir="ltr">#{o.id}</span>
        <span className="text-xs text-muted-foreground">{fmt.relativeTime(new Date(o.created_at))}</span>
      </span>
      <span className="flex items-center justify-between gap-3">
        <span className="font-medium" dir="auto">{o.title}</span>
        <Arrow className="size-4 shrink-0 text-muted-foreground" aria-hidden />
      </span>
      <span className="text-xs text-muted-foreground">
        {o.status === "planning" ? t("overview.planning") : t("overview.progress", { done: o.progress.completed, total: o.progress.total })}
        {o.progress.with_people > 0 ? ` · ${t("overview.withPeople", { n: o.progress.with_people })}` : ""}
        {o.progress.under_review > 0 ? ` · ${t("overview.underReview", { n: o.progress.under_review })}` : ""}
      </span>
    </Link>
  );
}

function Objectives({ d }: { d: Dashboard }) {
  const t = useTranslations("workforce.overview");
  return (
    <section aria-labelledby="wf-objectives-h" className="flex flex-col gap-3">
      <h2 id="wf-objectives-h" className="text-lg font-semibold">{t("objectives")}</h2>
      {d.objectives.latest.length === 0 ? (
        <Card className="p-6 text-center text-sm text-muted-foreground">{t("noObjectives")}</Card>
      ) : (
        <ul className="flex flex-col gap-3" data-testid="wf-objectives">
          {d.objectives.latest.map((o) => <li key={o.id}><ObjectiveRow o={o} /></li>)}
        </ul>
      )}
    </section>
  );
}

function Recent({ d }: { d: Dashboard }) {
  const t = useTranslations("workforce");
  const fmt = useFormatter();
  const actor = useActor();
  if (d.recent.length === 0) return null;
  return (
    <section aria-labelledby="wf-recent-h" className="flex flex-col gap-3">
      <h2 id="wf-recent-h" className="text-lg font-semibold">{t("overview.recent")}</h2>
      <Card className="p-0">
        <ul className="divide-y" data-testid="wf-recent">
          {d.recent.slice(0, 10).map((r) => {
            const key = `activity.${r.action.replace(/^workforce\./, "")}`;
            return (
              <li key={r.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 text-sm">
                <span className={cn("font-medium", r.level === "warning" && "text-foreground")}>{t.has(key) ? t(key) : r.action}</span>
                {r.task_id && (
                  <Link href={`/workforce/tasks?task=${r.task_id}`} className="font-mono text-xs text-primary underline underline-offset-4" dir="ltr">#{r.task_id}</Link>
                )}
                <span className="text-xs text-muted-foreground">{actor(r.actor)}</span>
                <span className="ms-auto text-xs text-muted-foreground">{fmt.relativeTime(new Date(r.at))}</span>
              </li>
            );
          })}
        </ul>
      </Card>
    </section>
  );
}
