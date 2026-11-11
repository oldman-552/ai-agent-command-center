"use client";
import { AlertTriangle, CheckCircle2, CircleAlert, CircleDashed, Info, KeyRound, Lock, LockOpen, Pencil, Plus, Power, RefreshCw, ScanSearch, Stethoscope, Trash2, XCircle } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import { useState } from "react";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { useCan } from "@/components/providers";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { accountTag, MockBadge, PageHeader, useActor, useFail, WorkforceTabs } from "@/components/workforce/shared";
import { useToasts } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { type Bridge, type Diagnosis, type DiagStep, useBridges, useWorkforceActions } from "@/lib/workforce";

export default function WorkforceBridgePage() {
  const t = useTranslations("workforce.bridge");
  const tc = useTranslations("common");
  const can = useCan();
  const canBridge = can("workforce.bridge") && can("credentials.manage");
  const list = useBridges();
  const [add, setAdd] = useState(false);
  const d = list.data;
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <PageHeader title={t("title")} subtitle={t("subtitle")}>
        {canBridge && <Button onClick={() => setAdd(true)} data-testid="wf-bridge-add"><Plus aria-hidden />{t("add")}</Button>}
      </PageHeader>
      <WorkforceTabs />

      <Card role="note" className="flex gap-3 border-warning p-4 text-sm" data-testid="wf-bridge-warning">
        <AlertTriangle className="mt-0.5 size-5 shrink-0 text-warning" aria-hidden />
        <div className="flex flex-col gap-1">
          <p className="font-medium">{t("warningTitle")}</p>
          <p>{t("warningBody")}</p>
          <p>{t("warningHuman")}</p>
        </div>
      </Card>

      <Card className="p-4 text-sm">
        <details>
          <summary className="cursor-pointer font-medium">{t("howTitle")}</summary>
          <ol className="ms-5 mt-3 flex list-decimal flex-col gap-1.5">
            {(["1", "2", "3", "4", "5", "6", "7"] as const).map((n) => <li key={n}>{t(`how.${n}`)}</li>)}
          </ol>
          {d?.in_container && <p className="mt-3 text-muted-foreground">{t("inContainer", { url: d.default_url })}</p>}
        </details>
      </Card>

      {list.isLoading && <Skeleton className="h-48" aria-busy="true" />}
      {list.isError && <Card role="alert" className="p-6 text-center text-destructive">{tc("error")}</Card>}
      {d && d.bridges.length === 0 && (
        <Card className="flex flex-col items-center gap-2 p-10 text-center" data-testid="wf-bridge-empty">
          <p className="font-medium">{t("empty")}</p>
          <p className="max-w-xl text-sm text-muted-foreground">{canBridge ? t("emptyHelp") : t("emptyHelpNoPermission")}</p>
        </Card>
      )}
      {d && d.bridges.length > 0 && (
        <ul className="flex flex-col gap-4">
          {d.bridges.map((b) => <li key={b.id}><BridgeCard b={b} /></li>)}
        </ul>
      )}
      {add && d && <AddDialog defaultUrl={d.default_url} inContainer={d.in_container} onClose={() => setAdd(false)} />}
    </div>
  );
}

function Flag({ ok, label }: { ok: boolean | null | undefined; label: string }) {
  const Icon = ok === true ? CheckCircle2 : ok === false ? XCircle : CircleDashed;
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium">
      <Icon className={cn("size-3.5", ok === true ? "text-success" : ok === false ? "text-destructive" : "text-muted-foreground")} aria-hidden />{label}
    </span>
  );
}

function BridgeCard({ b }: { b: Bridge }) {
  const t = useTranslations("workforce.bridge");
  const tc = useTranslations("common");
  const can = useCan();
  const canBridge = can("workforce.bridge");
  const canKey = canBridge && can("credentials.manage");
  const manage = can("workforce.manage");
  const a = useWorkforceActions();
  const fail = useFail();
  const fmt = useFormatter();
  const actor = useActor();
  const toast = useToasts((s) => s.push);
  const [diag, setDiag] = useState<Diagnosis>();
  const [dialog, setDialog] = useState<"terms" | "key" | "edit" | "delete">();
  const h = b.health;
  const fresh = b.health_fresh;
  return (
    <Card className="flex flex-col gap-4 p-5" data-testid={`wf-bridge-card-${b.id}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <h2 className="flex flex-wrap items-center gap-2 text-lg font-semibold" dir="auto">{b.name}{b.is_mock && <MockBadge short />}</h2>
          <p className="break-all font-mono text-xs text-muted-foreground" dir="ltr">{b.base_url}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          {manage && (
            <Button size="sm" disabled={a.testBridge.isPending} data-testid={`wf-bridge-test-${b.id}`}
              onClick={() => a.testBridge.mutate(b.id, { onError: fail, onSuccess: setDiag })}>
              <Stethoscope aria-hidden />{a.testBridge.isPending ? t("testing") : t("test")}
            </Button>
          )}
          {manage && (
            <Button size="sm" variant="outline" disabled={a.discover.isPending}
              onClick={() => a.discover.mutate(b.id, { onError: fail, onSuccess: (r) => toast("success", t("discovered", { n: r.discovered })) })}>
              <ScanSearch aria-hidden />{t("discover")}
            </Button>
          )}
          {manage && (
            <Button size="sm" variant="outline" disabled={a.refreshHealth.isPending} onClick={() => a.refreshHealth.mutate(b.id, { onError: fail })}>
              <RefreshCw aria-hidden />{t("refresh")}
            </Button>
          )}
        </div>
      </div>

      <div className="flex flex-wrap gap-2" data-testid={`wf-bridge-flags-${b.id}`}>
        <Flag ok={b.enabled} label={b.enabled ? t("flag.enabled") : t("flag.disabled")} />
        <Flag ok={h ? h.reachable : null} label={h ? (h.reachable ? t("flag.reachable") : t("flag.unreachable")) : t("flag.notChecked")} />
        <Flag ok={h?.reachable ? (h.key_ok ?? null) : null} label={t("flag.key")} />
        <Flag ok={h?.reachable ? (h.ready ?? null) : null} label={t("flag.ready")} />
        <Flag ok={b.terms.acknowledged} label={b.terms.acknowledged ? t("flag.terms") : t("flag.noTerms")} />
      </div>
      {h && !fresh && <p className="text-sm text-muted-foreground">{t("stale", { when: fmt.relativeTime(new Date(h.checked_at)) })}</p>}
      {h?.error && <p className="break-words text-sm text-destructive" lang="en" dir="ltr">{h.error}</p>}
      {b.is_mock && <p className="text-sm">{t("fake")}</p>}

      {!b.terms.acknowledged && (
        <div role="note" className="flex gap-3 rounded-md border border-warning p-3 text-sm" data-testid={`wf-bridge-locked-${b.id}`}>
          <Lock className="mt-0.5 size-5 shrink-0 text-warning" aria-hidden />
          <p>{t("locked")}</p>
        </div>
      )}
      {b.terms.acknowledged && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <LockOpen className="size-4 text-success" aria-hidden />
          {t("ackedBy", { who: actor(b.terms.by), when: b.terms.at ? fmt.dateTime(new Date(b.terms.at), { dateStyle: "medium", timeStyle: "short" }) : "—" })}
        </p>
      )}

      {h?.accounts && h.accounts.length > 0 && (
        <section aria-label={t("accounts")} className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold">{t("accounts")}</h3>
          <ul className="grid gap-2 sm:grid-cols-2" data-testid={`wf-bridge-accounts-${b.id}`}>
            {h.accounts.map((acc) => {
              const sessions = (h.sessions ?? []).filter((s) => s.account_hash === acc.hash).length;
              return (
                <li key={acc.hash} className="flex flex-wrap items-center gap-2 rounded-md border p-2.5 text-sm">
                  <span className="font-mono text-xs" dir="ltr">{accountTag(acc.account, acc.hash)}</span>
                  <Flag ok={!acc.disabled && !acc.error} label={acc.disabled ? t("accountDisabled") : acc.error ? t("accountError") : t("accountReady")} />
                  <span className="text-xs text-muted-foreground">{t("sessions", { n: sessions })}</span>
                </li>
              );
            })}
          </ul>
          <p className="text-xs text-muted-foreground">{t("accountsHelp")}</p>
        </section>
      )}
      {h?.queue && <p className="text-sm text-muted-foreground">{t("queue", { active: h.queue.active, depth: h.queue.depth, max: h.queue.max_depth })}</p>}

      {diag && <Diagnostics diag={diag} />}

      {(canBridge || canKey) && (
        <div className="flex flex-wrap gap-2 border-t pt-4">
          {canBridge && (b.terms.acknowledged
            ? <Button size="sm" variant="outline" disabled={a.revoke.isPending} onClick={() => a.revoke.mutate(b.id, { onError: fail })} data-testid={`wf-bridge-revoke-${b.id}`}><Lock aria-hidden />{t("revoke")}</Button>
            : <Button size="sm" onClick={() => setDialog("terms")} data-testid={`wf-bridge-ack-${b.id}`}><LockOpen aria-hidden />{t("acknowledge")}</Button>)}
          {canBridge && <Button size="sm" variant="outline" onClick={() => setDialog("edit")}><Pencil aria-hidden />{tc("edit")}</Button>}
          {canKey && <Button size="sm" variant="outline" onClick={() => setDialog("key")}><KeyRound aria-hidden />{t("replaceKey")}</Button>}
          {canBridge && (
            <Button size="sm" variant="outline" disabled={a.updateBridge.isPending}
              onClick={() => a.updateBridge.mutate({ id: b.id, patch: { enabled: !b.enabled } }, { onError: fail })}>
              <Power aria-hidden />{b.enabled ? t("disable") : t("enable")}
            </Button>
          )}
          {canBridge && <Button size="sm" variant="ghost" className="ms-auto" onClick={() => setDialog("delete")}><Trash2 aria-hidden />{t("delete")}</Button>}
        </div>
      )}

      {dialog === "terms" && <TermsDialog b={b} onClose={() => setDialog(undefined)} />}
      {dialog === "key" && <KeyDialog b={b} onClose={() => setDialog(undefined)} />}
      {dialog === "edit" && <EditDialog b={b} onClose={() => setDialog(undefined)} />}
      <ConfirmDialog open={dialog === "delete"} onOpenChange={(o) => !o && setDialog(undefined)} title={t("deleteTitle", { name: b.name })}
        description={t("deleteHelp")} confirmLabel={t("delete")} destructive busy={a.removeBridge.isPending}
        onConfirm={() => a.removeBridge.mutate(b.id, { onError: (e) => { fail(e); setDialog(undefined); }, onSuccess: () => setDialog(undefined) })} />
    </Card>
  );
}

function Diagnostics({ diag }: { diag: Diagnosis }) {
  const t = useTranslations("workforce.bridge");
  const hint = (s: DiagStep) => {
    const key = s.code && t.has(`hints.${s.step}_${s.code}`) ? `hints.${s.step}_${s.code}` : `hints.${s.step}`;
    return t.has(key) ? t(key) : s.hint;
  };
  return (
    <section aria-labelledby="wf-diag-h" className="flex flex-col gap-2 rounded-md border p-3" data-testid="wf-diagnostics">
      <h3 id="wf-diag-h" className="flex flex-wrap items-center gap-2 text-sm font-semibold">
        {t("diagTitle")}
        <span data-testid="wf-diag-result">{diag.ok ? t("diagOk") : t("diagFailed")}</span>
      </h3>
      <p className="text-xs text-muted-foreground">{t("diagHelp")}</p>
      <ol className="flex flex-col gap-2">
        {diag.steps.map((s) => {
          const Icon = s.ok === true ? CheckCircle2 : s.ok === false ? XCircle : CircleAlert;
          const needsHint = s.ok !== true;
          return (
            <li key={s.step} className="flex gap-2 text-sm" data-testid={`wf-diag-${s.step}`} data-ok={String(s.ok)}>
              <Icon className={cn("mt-0.5 size-4 shrink-0", s.ok === true ? "text-success" : s.ok === false ? "text-destructive" : "text-warning")} aria-hidden />
              <div className="flex min-w-0 flex-col">
                <span className="font-medium">{t.has(`steps.${s.step}`) ? t(`steps.${s.step}`) : s.step} — {s.ok === true ? t("stepOk") : s.ok === false ? t("stepFailed") : t("stepWarning")}</span>
                <span className="break-words text-xs text-muted-foreground" lang="en" dir="ltr">{s.detail}</span>
                {needsHint && hint(s) && <span className="text-xs">{hint(s)}</span>}
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function AddDialog({ defaultUrl, inContainer, onClose }: { defaultUrl: string; inContainer: boolean; onClose: () => void }) {
  const t = useTranslations("workforce.bridge");
  const tc = useTranslations("common");
  const a = useWorkforceActions();
  const fail = useFail();
  const [name, setName] = useState("");
  const [url, setUrl] = useState(defaultUrl);
  const [key, setKey] = useState("");
  const save = (e: React.FormEvent) => {
    e.preventDefault();
    a.createBridge.mutate({ name: name.trim(), base_url: url.trim(), api_key: key }, { onError: fail, onSuccess: onClose });
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")}>
        <DialogTitle>{t("addTitle")}</DialogTitle>
        <DialogDescription>{t("addHelp")}</DialogDescription>
        <form onSubmit={save} className="mt-4 flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wf-b-name">{t("name")}</Label>
            <Input id="wf-b-name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={128} dir="auto" data-testid="wf-b-name" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wf-b-url">{t("address")}</Label>
            <Input id="wf-b-url" value={url} onChange={(e) => setUrl(e.target.value)} required maxLength={512} dir="ltr" inputMode="url"
              aria-describedby="wf-b-url-help" data-testid="wf-b-url" />
            <p id="wf-b-url-help" className="text-xs text-muted-foreground">{inContainer ? t("addressHelpDocker") : t("addressHelp")}</p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wf-b-key">{t("key")}</Label>
            <Input id="wf-b-key" type="password" value={key} onChange={(e) => setKey(e.target.value)} required minLength={8} maxLength={512} dir="ltr"
              autoComplete="off" aria-describedby="wf-b-key-help" data-testid="wf-b-key" />
            <p id="wf-b-key-help" className="text-xs text-muted-foreground">{t("keyHelp")}</p>
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>{tc("cancel")}</Button>
            <Button type="submit" disabled={a.createBridge.isPending} data-testid="wf-b-save">{tc("create")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function KeyDialog({ b, onClose }: { b: Bridge; onClose: () => void }) {
  const t = useTranslations("workforce.bridge");
  const tc = useTranslations("common");
  const a = useWorkforceActions();
  const fail = useFail();
  const toast = useToasts((s) => s.push);
  const [key, setKey] = useState("");
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")}>
        <DialogTitle>{t("replaceKey")}</DialogTitle>
        <DialogDescription>{t("replaceKeyHelp", { hint: b.key_hint ?? "—" })}</DialogDescription>
        <form className="mt-4 flex flex-col gap-4" onSubmit={(e) => {
          e.preventDefault();
          a.setBridgeKey.mutate({ id: b.id, api_key: key }, { onError: fail, onSuccess: () => { toast("success", tc("saved")); onClose(); } });
        }}>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wf-b-newkey">{t("key")}</Label>
            <Input id="wf-b-newkey" type="password" value={key} onChange={(e) => setKey(e.target.value)} required minLength={8} maxLength={512} dir="ltr" autoComplete="off" />
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>{tc("cancel")}</Button>
            <Button type="submit" disabled={a.setBridgeKey.isPending}>{tc("save")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function EditDialog({ b, onClose }: { b: Bridge; onClose: () => void }) {
  const t = useTranslations("workforce.bridge");
  const tc = useTranslations("common");
  const a = useWorkforceActions();
  const fail = useFail();
  const toast = useToasts((s) => s.push);
  const s = b.settings;
  const [name, setName] = useState(b.name);
  const [url, setUrl] = useState(b.base_url);
  const [host, setHost] = useState(s.host_header);
  const [stream, setStream] = useState(s.stream);
  const [idle, setIdle] = useState(String(s.idle_timeout_s));
  const [total, setTotal] = useState(String(s.request_timeout_s));
  const [replay, setReplay] = useState(String(s.replay_window_s));
  const [registry, setRegistry] = useState(s.read_session_registry);
  const addressChanged = url.trim() !== b.base_url;
  const save = (e: React.FormEvent) => {
    e.preventDefault();
    a.updateBridge.mutate({ id: b.id, patch: {
      name: name.trim(), ...(addressChanged ? { base_url: url.trim() } : {}),
      settings: { host_header: host.trim(), stream, idle_timeout_s: Number(idle), request_timeout_s: Number(total),
        replay_window_s: Number(replay), read_session_registry: registry },
    } }, { onError: fail, onSuccess: () => { toast("success", tc("saved")); onClose(); } });
  };
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-h-[90dvh] overflow-y-auto">
        <DialogTitle>{t("editTitle", { name: b.name })}</DialogTitle>
        <DialogDescription>{t("editHelp")}</DialogDescription>
        <form onSubmit={save} className="mt-4 flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wf-e-name">{t("name")}</Label>
            <Input id="wf-e-name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={128} dir="auto" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wf-e-url">{t("address")}</Label>
            <Input id="wf-e-url" value={url} onChange={(e) => setUrl(e.target.value)} required maxLength={512} dir="ltr" aria-describedby="wf-e-url-help" />
            <p id="wf-e-url-help" className={cn("text-xs", addressChanged && b.terms.acknowledged ? "font-medium" : "text-muted-foreground")}>{t("addressChangeHelp")}</p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wf-e-host">{t("hostHeader")} <span className="font-normal text-muted-foreground">({tc("optional")})</span></Label>
            <Input id="wf-e-host" value={host} onChange={(e) => setHost(e.target.value)} maxLength={64} dir="ltr" aria-describedby="wf-e-host-help" />
            <p id="wf-e-host-help" className="text-xs text-muted-foreground">{t("hostHeaderHelp", { value: s.effective_host_header ?? "127.0.0.1" })}</p>
          </div>
          <div className="grid gap-4 sm:grid-cols-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="wf-e-idle">{t("idleTimeout")}</Label>
              <Input id="wf-e-idle" type="number" min={5} max={600} value={idle} onChange={(e) => setIdle(e.target.value)} required />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="wf-e-total">{t("totalTimeout")}</Label>
              <Input id="wf-e-total" type="number" min={30} max={7200} value={total} onChange={(e) => setTotal(e.target.value)} required />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="wf-e-replay">{t("replayWindow")}</Label>
              <Input id="wf-e-replay" type="number" min={0} max={3600} value={replay} onChange={(e) => setReplay(e.target.value)} required />
            </div>
          </div>
          <p className="text-xs text-muted-foreground">{t("timeoutsHelp")}</p>
          <div className="flex items-start gap-3">
            <input id="wf-e-stream" type="checkbox" checked={stream} onChange={(e) => setStream(e.target.checked)} className="mt-1 size-4 accent-primary" />
            <div className="flex flex-col"><Label htmlFor="wf-e-stream">{t("stream")}</Label><p className="text-xs text-muted-foreground">{t("streamHelp")}</p></div>
          </div>
          <div className="flex items-start gap-3">
            <input id="wf-e-registry" type="checkbox" checked={registry} onChange={(e) => setRegistry(e.target.checked)} className="mt-1 size-4 accent-primary" />
            <div className="flex flex-col"><Label htmlFor="wf-e-registry">{t("registry")}</Label><p className="text-xs text-muted-foreground">{t("registryHelp")}</p></div>
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>{tc("cancel")}</Button>
            <Button type="submit" disabled={a.updateBridge.isPending}>{tc("save")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** The terms gate: nothing is sent to Arena for this bridge until a person accepts the exact statement shown. */
function TermsDialog({ b, onClose }: { b: Bridge; onClose: () => void }) {
  const t = useTranslations("workforce.bridge");
  const tc = useTranslations("common");
  const a = useWorkforceActions();
  const fail = useFail();
  const [checked, setChecked] = useState(false);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-h-[90dvh] overflow-y-auto">
        <DialogTitle>{t("ackTitle", { name: b.name })}</DialogTitle>
        <DialogDescription>{t("ackHelp")}</DialogDescription>
        <div className="mt-4 flex flex-col gap-4 text-sm">
          <a href={b.terms.terms_url} target="_blank" rel="noreferrer noopener" className="w-fit font-medium text-primary underline underline-offset-4">{t("termsLink")}</a>
          <p className="flex gap-2"><Info className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden />{t("statementMeaning")}</p>
          <blockquote className="rounded-md border bg-muted p-3" lang="en" dir="ltr" data-testid="wf-terms-statement">{b.terms.statement}</blockquote>
          <div className="flex items-start gap-3">
            <input id="wf-terms-check" type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} className="mt-1 size-4 accent-primary" data-testid="wf-terms-check" />
            <Label htmlFor="wf-terms-check">{t("ackCheckbox")}</Label>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose}>{tc("cancel")}</Button>
            <Button disabled={!checked || a.acknowledge.isPending} data-testid="wf-terms-confirm"
              onClick={() => a.acknowledge.mutate({ id: b.id, statement: b.terms.statement }, { onError: fail, onSuccess: onClose })}>{t("acknowledge")}</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
