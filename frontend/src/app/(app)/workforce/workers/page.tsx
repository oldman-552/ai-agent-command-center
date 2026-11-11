"use client";
import { AlertTriangle, Settings2 } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
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
import { accountTag, MockBadge, ModeBadge, PageHeader, useFail, useReason, WorkforceTabs } from "@/components/workforce/shared";
import { useToasts } from "@/lib/toast";
import { type Mode, type OptionProvider, type Options, useWfOptions, useWorkforce, useWorkforceActions, type Worker, type Workforce } from "@/lib/workforce";

export default function WorkforceWorkersPage() {
  const t = useTranslations("workforce");
  const tc = useTranslations("common");
  const can = useCan();
  const manage = can("workforce.manage");
  const wf = useWorkforce();
  const options = useWfOptions(manage);
  const [editManager, setEditManager] = useState(false);
  const [editWorker, setEditWorker] = useState<Worker>();
  const force = wf.data?.workforce;
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <PageHeader title={t("workers.title")} subtitle={t("workers.subtitle")} />
      <WorkforceTabs />
      {wf.isLoading && <Skeleton className="h-64" aria-busy="true" />}
      {wf.isError && <Card role="alert" className="p-6 text-center text-destructive">{tc("error")}</Card>}
      {wf.data && !force && <Card className="p-6 text-sm text-muted-foreground">{t("notSetUp")}</Card>}
      {force && (
        <>
          <ManagerCard force={force} onEdit={manage ? () => setEditManager(true) : undefined} />
          <section aria-labelledby="wf-workers-h" className="flex flex-col gap-3">
            <h2 id="wf-workers-h" className="text-lg font-semibold">{t("workers.four")}</h2>
            <ul className="grid gap-3 md:grid-cols-2">
              {force.workers.map((w) => <li key={w.id}><WorkerCard w={w} onEdit={manage ? () => setEditWorker(w) : undefined} /></li>)}
            </ul>
            <p className="text-sm text-muted-foreground">{t("binding.notAttested")}</p>
          </section>
        </>
      )}
      {force && editManager && options.data && <ManagerDialog force={force} options={options.data} onClose={() => setEditManager(false)} />}
      {editWorker && options.data && <WorkerDialog w={editWorker} options={options.data} onClose={() => setEditWorker(undefined)} />}
    </div>
  );
}

function ManagerCard({ force, onEdit }: { force: Workforce; onEdit?: () => void }) {
  const t = useTranslations("workforce");
  const reason = useReason();
  const m = force.manager;
  return (
    <section aria-labelledby="wf-manager-h">
      <Card className="flex flex-col gap-3 p-5" data-testid="wf-manager-card">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h2 id="wf-manager-h" className="text-lg font-semibold">{t("manager.title")}</h2>
            <p className="text-sm text-muted-foreground">{t("manager.help")}</p>
          </div>
          {onEdit && <Button variant="outline" onClick={onEdit} data-testid="wf-manager-edit"><Settings2 aria-hidden />{t("workers.configure")}</Button>}
        </div>
        {!m.configured && (
          <div role="note" className="flex gap-3 rounded-md border border-warning p-3 text-sm">
            <AlertTriangle className="mt-0.5 size-5 shrink-0 text-warning" aria-hidden />
            <p>{t("manager.notConfigured", { problem: reason(m.problem) })}</p>
          </div>
        )}
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[12rem_1fr]">
          <dt className="text-muted-foreground">{t("manager.model")}</dt>
          <dd className="flex flex-wrap items-center gap-2" data-testid="wf-manager-model">
            {m.model ? <><span dir="auto">{m.model.provider}</span> · <span className="font-mono" dir="ltr">{m.model.model}</span></> : t("manager.noModel")}
            {m.is_mock && <MockBadge />}
          </dd>
          <dt className="text-muted-foreground">{t("manager.fallback")}</dt>
          <dd>{m.fallback ? <><span dir="auto">{m.fallback.provider}</span> · <span className="font-mono" dir="ltr">{m.fallback.model}</span></> : t("manager.noFallback")}</dd>
          <dt className="text-muted-foreground">{t("manager.policy")}</dt>
          <dd>{t(`policy.${force.review_policy}`)}</dd>
          <dt className="text-muted-foreground">{t("manager.maxRevisions")}</dt>
          <dd className="tabular-nums">{force.max_revisions}</dd>
        </dl>
        {m.is_mock && <p className="text-sm">{t("overview.managerMock")}</p>}
      </Card>
    </section>
  );
}

function WorkerCard({ w, onEdit }: { w: Worker; onEdit?: () => void }) {
  const t = useTranslations("workforce");
  const reason = useReason();
  const b = w.account_binding;
  return (
    <Card className="flex h-full flex-col gap-3 p-5" data-testid={`wf-worker-card-${w.slot}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="font-semibold" dir="auto"><span className="font-mono text-primary" dir="ltr">{w.label}</span> {w.name}</h3>
        <ModeBadge mode={w.mode} />
      </div>
      <p className="text-sm" data-testid="wf-worker-status">
        <span className="font-medium">{t(`workerStatus.${w.status.state}`)}</span>{w.status.reason ? ` — ${reason(w.status.reason)}` : ""}
      </p>
      <dl className="grid gap-x-4 gap-y-1.5 text-sm sm:grid-cols-[9rem_1fr]">
        <dt className="text-muted-foreground">{t("workers.role")}</dt><dd><bdi>{w.role}</bdi></dd>
        <dt className="text-muted-foreground">{t("workers.skills")}</dt><dd><span dir="ltr">{w.skills.join(", ") || "—"}</span></dd>
        <dt className="text-muted-foreground">{t("workers.provider")}</dt>
        <dd className="flex flex-wrap items-center gap-2">
          {w.provider ? <span dir="auto">{w.provider.name}</span> : t("workers.noProvider")}
          {w.provider?.is_mock && <MockBadge short />}
        </dd>
        {w.model && (<><dt className="text-muted-foreground">{t("workers.session")}</dt><dd className="break-all"><span className="font-mono text-xs" dir="ltr">{w.model}</span></dd></>)}
        {w.mode === "AUTOMATIC" && (
          <>
            <dt className="text-muted-foreground">{t("workers.account")}</dt>
            <dd data-testid="wf-worker-binding">
              {t(`binding.${b.type}`)}{b.account ? <> · <span className="font-mono" dir="ltr">{accountTag(b.account, b.account_hash)}</span></> : null}
            </dd>
          </>
        )}
        <dt className="text-muted-foreground">{t("workers.person")}</dt><dd>{w.assignee ? w.assignee.username : t("workers.anyone")}</dd>
        <dt className="text-muted-foreground">{t("workers.onFailure")}</dt><dd>{w.fallback_to_human ? t("workers.fallbackOn") : t("workers.fallbackOff")}</dd>
      </dl>
      {w.shares_session_with.length > 0 && (
        <p role="note" className="flex gap-2 text-sm"><AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
          {t("workers.sharedSession", { others: w.shares_session_with.map((s) => `W${s}`).join(", ") })}</p>
      )}
      <div className="mt-auto flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">{t("workers.busy", { n: w.busy_tasks })}</span>
        {onEdit && <Button variant="outline" size="sm" onClick={onEdit} data-testid={`wf-worker-edit-${w.slot}`}><Settings2 aria-hidden />{t("workers.configure")}</Button>}
      </div>
    </Card>
  );
}

/** Provider plus model. For an Arena Local Bridge the "model" is a session of one account, or a pooled name. */
function RoutePicker({ idPrefix, providers, providerId, model, onChange, allowNone }: {
  idPrefix: string; providers: OptionProvider[]; providerId: number | null; model: string;
  onChange: (providerId: number | null, model: string) => void; allowNone?: boolean;
}) {
  const t = useTranslations("workforce.workers");
  const p = providers.find((x) => x.id === providerId);
  const choices: { value: string; label: string }[] = [];
  if (p?.is_bridge) {
    for (const s of p.sessions ?? []) {
      choices.push({ value: s.id, label: [s.title, s.model, s.account && accountTag(s.account, s.account_hash)].filter(Boolean).join(" — ") || s.id });
    }
    for (const m of p.pooled_models ?? []) choices.push({ value: m, label: t("pooledModel", { name: m }) });
  } else if (p) {
    for (const m of p.models) {
      const name = typeof m === "string" ? m : m.name;
      choices.push({ value: name, label: typeof m === "string" ? m : (m.display_name || m.name) });
    }
  }
  if (model && choices.length > 0 && !choices.some((c) => c.value === model)) choices.unshift({ value: model, label: model });
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${idPrefix}-provider`}>{t("provider")}</Label>
        <NativeSelect id={`${idPrefix}-provider`} value={providerId ?? ""} data-testid={`${idPrefix}-provider`}
          onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null, "")}>
          {(allowNone || providerId === null) && <option value="">{t("noProvider")}</option>}
          {providers.map((x) => (
            <option key={x.id} value={x.id}>{x.name}{x.enabled ? "" : ` (${t("providerDisabled")})`}</option>
          ))}
        </NativeSelect>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor={`${idPrefix}-model`}>{p?.is_bridge ? t("session") : t("model")}</Label>
        {choices.length > 0 ? (
          <NativeSelect id={`${idPrefix}-model`} value={model} onChange={(e) => onChange(providerId, e.target.value)} disabled={!p} data-testid={`${idPrefix}-model`}>
            <option value="">{t("chooseModel")}</option>
            {choices.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
          </NativeSelect>
        ) : (
          <Input id={`${idPrefix}-model`} value={model} onChange={(e) => onChange(providerId, e.target.value)} disabled={!p} maxLength={128} dir="ltr"
            data-testid={`${idPrefix}-model`} />
        )}
        {p?.is_bridge && <p className="text-xs text-muted-foreground">{choices.length > 0 ? t("sessionHelp") : t("sessionHelpEmpty")}</p>}
      </div>
    </div>
  );
}

function ManagerDialog({ force, options, onClose }: { force: Workforce; options: Options; onClose: () => void }) {
  const t = useTranslations("workforce");
  const tc = useTranslations("common");
  const a = useWorkforceActions();
  const fail = useFail();
  const toast = useToasts((s) => s.push);
  const m = force.manager;
  const usable = options.providers.filter((p) => !p.is_bridge);  // a bridge session cannot be the Manager
  const [providerId, setProviderId] = useState<number | null>(m.model?.provider_id ?? null);
  const [model, setModel] = useState(m.model?.model ?? "");
  const [fbProvider, setFbProvider] = useState<number | null>(m.fallback?.provider_id ?? null);
  const [fbModel, setFbModel] = useState(m.fallback?.model ?? "");
  const [policy, setPolicy] = useState(force.review_policy);
  const [maxRevisions, setMaxRevisions] = useState(String(force.max_revisions));
  const [timeout, setTimeoutS] = useState(String(m.timeout_seconds ?? 600));
  const [instructions, setInstructions] = useState(m.instructions ?? "");
  const chosen = usable.find((p) => p.id === providerId);
  const save = (e: React.FormEvent) => {
    e.preventDefault();
    a.updateManager.mutate({
      provider_id: providerId, model: model || null, fallback_provider_id: fbProvider, fallback_model: fbModel || null,
      review_policy: policy, max_revisions: Number(maxRevisions), timeout_seconds: Number(timeout), instructions,
    }, { onError: fail, onSuccess: () => { toast("success", tc("saved")); onClose(); } });
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-h-[90dvh] max-w-2xl overflow-y-auto">
        <DialogTitle>{t("manager.editTitle")}</DialogTitle>
        <DialogDescription>{t("manager.editHelp")}</DialogDescription>
        <form onSubmit={save} className="mt-4 flex flex-col gap-4">
          <RoutePicker idPrefix="wf-m" providers={usable} providerId={providerId} model={model} allowNone
            onChange={(p, mo) => { setProviderId(p); setModel(mo); }} />
          {chosen?.is_mock && (
            <p role="note" className="flex gap-2 rounded-md border border-warning p-3 text-sm">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />{t("overview.managerMock")}
            </p>
          )}
          <fieldset className="flex flex-col gap-3 rounded-md border p-3">
            <legend className="px-1 text-sm font-medium">{t("manager.fallback")} <span className="font-normal text-muted-foreground">({tc("optional")})</span></legend>
            <RoutePicker idPrefix="wf-mf" providers={usable} providerId={fbProvider} model={fbModel} allowNone
              onChange={(p, mo) => { setFbProvider(p); setFbModel(mo); }} />
          </fieldset>
          <div className="grid gap-4 sm:grid-cols-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="wf-m-policy">{t("manager.policy")}</Label>
              <NativeSelect id="wf-m-policy" value={policy} onChange={(e) => setPolicy(e.target.value as "manager" | "human")}>
                <option value="manager">{t("policy.manager")}</option>
                <option value="human">{t("policy.human")}</option>
              </NativeSelect>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="wf-m-rev">{t("manager.maxRevisions")}</Label>
              <Input id="wf-m-rev" type="number" min={0} max={10} value={maxRevisions} onChange={(e) => setMaxRevisions(e.target.value)} required />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="wf-m-timeout">{t("workers.timeout")}</Label>
              <Input id="wf-m-timeout" type="number" min={30} max={3600} value={timeout} onChange={(e) => setTimeoutS(e.target.value)} required />
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wf-m-instructions">{t("manager.instructions")} <span className="font-normal text-muted-foreground">({tc("optional")})</span></Label>
            <Textarea id="wf-m-instructions" value={instructions} onChange={(e) => setInstructions(e.target.value)} maxLength={8000} dir="auto"
              aria-describedby="wf-m-instructions-help" />
            <p id="wf-m-instructions-help" className="text-xs text-muted-foreground">{t("manager.instructionsHelp")}</p>
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>{tc("cancel")}</Button>
            <Button type="submit" disabled={a.updateManager.isPending} data-testid="wf-manager-save">{a.updateManager.isPending ? tc("saving") : tc("save")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function WorkerDialog({ w, options, onClose }: { w: Worker; options: Options; onClose: () => void }) {
  const t = useTranslations("workforce");
  const tc = useTranslations("common");
  const a = useWorkforceActions();
  const fail = useFail();
  const toast = useToasts((s) => s.push);
  const [name, setName] = useState(w.name);
  const [role, setRole] = useState(w.role);
  const [skills, setSkills] = useState(w.skills.join(", "));
  const [mode, setMode] = useState<Mode>(w.mode);
  const [providerId, setProviderId] = useState<number | null>(w.provider?.id ?? null);
  const [model, setModel] = useState(w.model ?? "");
  const [fallback, setFallback] = useState(w.fallback_to_human);
  const [assignee, setAssignee] = useState(w.assignee ? String(w.assignee.id) : "");
  const [timeout, setTimeoutS] = useState(String(w.timeout_seconds));
  const [attempts, setAttempts] = useState(String(w.retry_policy?.max_attempts ?? 1));
  const [prompt, setPrompt] = useState(w.prompt ?? "");
  const chosen = options.providers.find((p) => p.id === providerId);
  const missing = mode === "AUTOMATIC" && (!providerId || !model);
  const save = (e: React.FormEvent) => {
    e.preventDefault();
    if (missing) return;
    a.updateWorker.mutate({ id: w.id, patch: {
      name, role, skills: skills.split(",").map((s) => s.trim()).filter(Boolean), mode, provider_id: providerId, model: model || null,
      fallback_to_human: fallback, assignee_user_id: assignee ? Number(assignee) : null, timeout_seconds: Number(timeout),
      retry_policy: { ...(w.retry_policy ?? { backoff_seconds: 0, backoff_multiplier: 1 }), max_attempts: Number(attempts) }, prompt,
    } }, { onError: fail, onSuccess: () => { toast("success", tc("saved")); onClose(); } });
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-h-[90dvh] max-w-2xl overflow-y-auto">
        <DialogTitle>{t("workers.editTitle", { label: w.label })}</DialogTitle>
        <DialogDescription>{t("workers.editHelp")}</DialogDescription>
        <form onSubmit={save} className="mt-4 flex flex-col gap-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="wf-w-name">{t("workers.name")}</Label>
              <Input id="wf-w-name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={128} dir="auto" />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="wf-w-role">{t("workers.role")}</Label>
              <Input id="wf-w-role" value={role} onChange={(e) => setRole(e.target.value)} required maxLength={64} dir="ltr" />
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wf-w-skills">{t("workers.skills")}</Label>
            <Input id="wf-w-skills" value={skills} onChange={(e) => setSkills(e.target.value)} dir="ltr" aria-describedby="wf-w-skills-help" />
            <p id="wf-w-skills-help" className="text-xs text-muted-foreground">{t("workers.skillsHelp")}</p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wf-w-mode">{t("workers.mode")}</Label>
            <NativeSelect id="wf-w-mode" value={mode} onChange={(e) => setMode(e.target.value as Mode)} aria-describedby="wf-w-mode-help" data-testid="wf-w-mode">
              {(["HUMAN_ASSISTED", "AUTOMATIC", "DISABLED"] as const).map((x) => <option key={x} value={x}>{t(`mode.${x}`)}</option>)}
            </NativeSelect>
            <p id="wf-w-mode-help" className="text-xs text-muted-foreground" data-testid="wf-w-mode-help">{t(`modeHelp.${mode}`)}</p>
          </div>
          <RoutePicker idPrefix="wf-w" providers={options.providers} providerId={providerId} model={model} allowNone
            onChange={(p, mo) => { setProviderId(p); setModel(mo); }} />
          {missing && <p role="alert" className="text-sm text-destructive">{t("errors.automatic_needs_provider_and_model")}</p>}
          {mode === "AUTOMATIC" && chosen?.is_bridge && !chosen.terms_acknowledged && (
            <p role="note" className="flex gap-2 rounded-md border border-warning p-3 text-sm">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
              <span>{t("workers.termsFirst")} <Link href="/workforce/bridge" className="font-medium text-primary underline underline-offset-4">{t("tabs.bridge")}</Link></span>
            </p>
          )}
          <div className="flex items-start gap-3">
            <input id="wf-w-fallback" type="checkbox" checked={fallback} onChange={(e) => setFallback(e.target.checked)} className="mt-1 size-4 accent-primary" />
            <div className="flex flex-col">
              <Label htmlFor="wf-w-fallback">{t("workers.fallbackLabel")}</Label>
              <p className="text-xs text-muted-foreground">{t("workers.fallbackHelp")}</p>
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="wf-w-person">{t("workers.person")}</Label>
              <NativeSelect id="wf-w-person" value={assignee} onChange={(e) => setAssignee(e.target.value)}>
                <option value="">{t("workers.anyone")}</option>
                {options.people.map((p) => <option key={p.id} value={p.id}>{p.username}</option>)}
              </NativeSelect>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="wf-w-timeout">{t("workers.timeout")}</Label>
              <Input id="wf-w-timeout" type="number" min={30} max={7200} value={timeout} onChange={(e) => setTimeoutS(e.target.value)} required />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="wf-w-attempts">{t("workers.attempts")}</Label>
              <Input id="wf-w-attempts" type="number" min={1} max={10} value={attempts} onChange={(e) => setAttempts(e.target.value)} required />
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wf-w-prompt">{t("workers.prompt")}</Label>
            <Textarea id="wf-w-prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} maxLength={20000} dir="auto" className="min-h-32"
              aria-describedby="wf-w-prompt-help" />
            <p id="wf-w-prompt-help" className="text-xs text-muted-foreground">{t("workers.promptHelp")}</p>
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>{tc("cancel")}</Button>
            <Button type="submit" disabled={a.updateWorker.isPending || missing} data-testid="wf-worker-save">{a.updateWorker.isPending ? tc("saving") : tc("save")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
