"use client";
import { AlertTriangle, CircleStop, FlaskConical, Globe, Info, Lock, LockOpen, Play, Power, RefreshCw, Send, SquarePlus } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
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
import { ApiError } from "@/lib/api";
import { type BridgeProvider, useBridge, useBridgeActions, useBridgeProfiles, useBridgeSession, useBridgeSessions, useBridgeStatus } from "@/lib/browser-bridge";
import { useUsers } from "@/lib/admin";
import { useToasts } from "@/lib/toast";
import { cn } from "@/lib/utils";

const STATE_TONE: Record<string, string> = {
  READY: "bg-success", COMPLETED: "bg-success", GENERATING: "bg-primary", SENDING: "bg-primary", STARTING: "bg-primary",
  AUTH_REQUIRED: "bg-warning", FAILED: "bg-destructive", STOPPED: "bg-muted-foreground", DISCONNECTED: "bg-muted-foreground",
};

function StateLabel({ state }: { state: string }) {
  const t = useTranslations("bridge");
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs font-medium" data-testid="bb-state">
      <span aria-hidden className={cn("size-2 rounded-full", STATE_TONE[state] ?? "bg-muted-foreground")} />
      {t.has(`states.${state}`) ? t(`states.${state}`) : state}
    </span>
  );
}

function useFail() {
  const t = useTranslations("bridge");
  const tc = useTranslations("common");
  const toast = useToasts((s) => s.push);
  return (e: unknown) => {
    const code = e instanceof ApiError ? e.code : "error";
    toast("error", t.has(`errors.${code}`) ? t(`errors.${code}`) : tc("requestFailed", { code }));
  };
}

export default function BrowserBridgePage() {
  const t = useTranslations("bridge");
  const can = useCan();
  const manage = can("browser_bridge.manage");
  const bridge = useBridge();
  const a = useBridgeActions();
  const fail = useFail();
  const [sel, setSel] = useState<BridgeProvider>();
  const [addSite, setAddSite] = useState(false);
  const [ack, setAck] = useState<BridgeProvider>();
  const [open, setOpen] = useState<number>();
  const d = bridge.data;
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold">{t("title")}
            <span className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium"><FlaskConical className="size-3.5 text-warning" aria-hidden />{t("experimental")}</span>
          </h1>
          <p className="max-w-3xl text-sm text-muted-foreground">{t("subtitle")}</p>
        </div>
        {d && manage && (
          <Button variant={d.enabled ? "outline" : "default"} disabled={a.setEnabled.isPending} data-testid="bb-toggle"
            onClick={() => a.setEnabled.mutate(!d.enabled, { onError: fail })}>
            <Power aria-hidden />{d.enabled ? t("disable") : t("enable")}
          </Button>
        )}
      </div>

      <Card role="note" className="flex gap-3 border-warning p-4 text-sm">
        <AlertTriangle className="mt-0.5 size-5 shrink-0 text-warning" aria-hidden />
        <div className="flex flex-col gap-1">
          <p className="font-medium">{t("warningTitle")}</p>
          <p>{t("warningBody")}</p>
        </div>
      </Card>

      <Card role="note" className="flex gap-3 p-4 text-sm" data-testid="bb-arena-moved">
        <Info className="mt-0.5 size-5 shrink-0 text-primary" aria-hidden />
        <div className="flex flex-col gap-1">
          <p className="font-medium">{t("arenaMovedTitle")}</p>
          <p>{t("arenaMovedBody")}</p>
          <Link href="/workforce/workers" className="w-fit font-medium text-primary underline underline-offset-4">{t("arenaMovedLink")}</Link>
        </div>
      </Card>

      {bridge.isLoading && <Skeleton className="h-40" aria-busy="true" />}
      {d && (
        <div className="grid gap-3 sm:grid-cols-3" data-testid="bb-status">
          <Card className="p-4"><p className="text-xs text-muted-foreground">{t("status")}</p><p className="text-lg font-semibold">{d.enabled ? t("on") : t("off")}</p></Card>
          <Card className="p-4"><p className="text-xs text-muted-foreground">{t("service")}</p><p className="text-lg font-semibold">{d.health.reachable ? t("reachable") : t("unreachable")}</p></Card>
          <Card className="p-4"><p className="text-xs text-muted-foreground">{t("liveSessions")}</p><p className="text-lg font-semibold tabular-nums">{d.health.reachable ? `${d.health.live_sessions} / ${d.health.max_sessions}` : "—"}</p></Card>
        </div>
      )}

      {d && (
        <section aria-labelledby="prov-h" className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 id="prov-h" className="text-lg font-semibold">{t("providers")}</h2>
            {manage && <Button size="sm" variant="outline" onClick={() => setAddSite(true)} data-testid="bb-add-site"><SquarePlus aria-hidden />{t("sites.add")}</Button>}
          </div>
          <ul className="grid gap-3 md:grid-cols-2">
            {d.providers.map((p) => (
              <li key={p.key}>
                <Card className="flex h-full flex-col gap-2 p-4" data-testid={`bb-provider-${p.key}`}>
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <h3 className="font-semibold" dir="auto">{p.name}</h3>
                    <span className="inline-flex items-center gap-1 text-xs font-medium">
                      {p.unlocked ? <LockOpen className="size-3.5 text-success" aria-hidden /> : <Lock className="size-3.5 text-muted-foreground" aria-hidden />}
                      {p.unlocked ? t("unlocked") : t("locked")}
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground">{p.verified ? t("verified") : t("unverified")}{p.overridden ? ` · ${t("selectors.overridden")}` : ""}</p>
                  {p.notes && <p className="text-xs text-muted-foreground" lang="en" dir="ltr">{p.notes}</p>}
                  {p.acknowledgement && <p className="text-xs text-muted-foreground">{t("ackedBy", { by: p.acknowledgement.by })}</p>}
                  {manage && (p.requires_terms || p.overridden) && (
                    <div className="mt-auto flex flex-wrap gap-2">
                      {p.custom && <Button size="sm" variant="ghost" onClick={() => a.removeSite.mutate(p.key, { onError: fail })} aria-label={`${t("sites.remove")}: ${p.name}`} data-testid={`bb-remove-${p.key}`}>{t("sites.remove")}</Button>}
                      {p.requires_terms && <Button size="sm" variant="ghost" onClick={() => setSel(p)} aria-label={`${t("selectors.edit")}: ${p.name}`} data-testid={`bb-sel-${p.key}`}>{t("selectors.edit")}</Button>}
                      {p.unlocked
                        ? <Button size="sm" variant="outline" onClick={() => a.revoke.mutate(p.key, { onError: fail })} aria-label={`${t("revoke")}: ${p.name}`}>{t("revoke")}</Button>
                        : <Button size="sm" variant="outline" onClick={() => setAck(p)} aria-label={`${t("acknowledge")}: ${p.name}`}>{t("acknowledge")}</Button>}
                    </div>
                  )}
                </Card>
              </li>
            ))}
          </ul>
        </section>
      )}

      {d && <Profiles manage={manage} />}
      {d && <Sessions providers={d.providers.filter((p) => p.unlocked)} enabled={d.enabled} manage={manage} onOpen={setOpen} />}
      {ack && <AckDialog p={ack} onClose={() => setAck(undefined)} />}
      {sel && <SelectorsDialog p={sel} onClose={() => setSel(undefined)} />}
      {addSite && <AddSiteDialog onClose={() => setAddSite(false)} />}
      {open && <SessionDialog id={open} manage={manage} onClose={() => setOpen(undefined)} />}
    </div>
  );
}

function AckDialog({ p, onClose }: { p: BridgeProvider; onClose: () => void }) {
  const t = useTranslations("bridge");
  const tc = useTranslations("common");
  const a = useBridgeActions();
  const fail = useFail();
  const [ok, setOk] = useState(false);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-w-lg">
        <DialogTitle>{t("ackTitle", { name: p.name })}</DialogTitle>
        <DialogDescription>{t("ackHelp")}</DialogDescription>
        <p className="mt-3 text-sm"><a className="text-primary underline" href={p.terms_url} target="_blank" rel="noreferrer noopener" dir="ltr">{p.terms_url}</a></p>
        <blockquote className="mt-3 rounded-md border bg-muted p-3 text-sm" lang="en" dir="ltr">{p.statement}</blockquote>
        <label className="mt-4 flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1 size-4" checked={ok} onChange={(e) => setOk(e.target.checked)} data-testid="bb-ack-check" />
          <span>{t("ackCheckbox")}</span>
        </label>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>{tc("cancel")}</Button>
          <Button disabled={!ok || a.acknowledge.isPending} data-testid="bb-ack-confirm"
            onClick={() => a.acknowledge.mutate({ provider: p.key, statement: p.statement ?? "" }, { onSuccess: onClose, onError: fail })}>{t("acknowledge")}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Sessions({ providers, enabled, manage, onOpen }: { providers: BridgeProvider[]; enabled: boolean; manage: boolean; onOpen: (id: number) => void }) {
  const t = useTranslations("bridge");
  const fmt = useFormatter();
  const sessions = useBridgeSessions();
  const a = useBridgeActions();
  const fail = useFail();
  const [provider, setProvider] = useState("");
  const [profile, setProfile] = useState("");
  const profiles = useBridgeProfiles();
  const chosen = provider || providers[0]?.key || "";
  return (
    <section aria-labelledby="sess-h" className="flex flex-col gap-3">
      <h2 id="sess-h" className="text-lg font-semibold">{t("sessions")}</h2>
      {manage && enabled && providers.length > 0 && (
        <Card className="p-4">
          <form className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end" onSubmit={(e) => {
            e.preventDefault();
            a.create.mutate({ provider: chosen, ...(profile ? { profile } : {}) }, { onSuccess: (s) => onOpen(s.id), onError: fail });
          }}>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="bb-provider">{t("provider")}</Label>
              <NativeSelect id="bb-provider" value={chosen} onChange={(e) => setProvider(e.target.value)}>
                {providers.map((p) => <option key={p.key} value={p.key}>{p.name}</option>)}
              </NativeSelect>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="bb-profile">{t("profile")}</Label>
              <NativeSelect id="bb-profile" value={profile} onChange={(e) => setProfile(e.target.value)} aria-describedby="bb-profile-help">
                <option value="">{t("profiles.mine")}</option>
                {profiles.data?.map((p) => <option key={p.name} value={p.name} disabled={!!p.active_session}>{p.label || p.name}{p.active_session ? ` (${t("profiles.open")})` : ""}</option>)}
              </NativeSelect>
            </div>
            <Button type="submit" disabled={a.create.isPending} data-testid="bb-start"><Play aria-hidden />{t("startSession")}</Button>
            <p id="bb-profile-help" className="text-xs text-muted-foreground sm:col-span-3">{t("profileHelp")}</p>
          </form>
        </Card>
      )}
      {!enabled && <p className="text-sm text-muted-foreground">{t("disabledHelp")}</p>}
      {sessions.data?.length === 0 && <p className="text-sm text-muted-foreground">{t("noSessions")}</p>}
      {!!sessions.data?.length && (
        <Card className="overflow-x-auto" tabIndex={0} aria-label={t("sessions")}>
          <table className="w-full min-w-[640px] text-sm" data-testid="bb-sessions">
            <thead className="border-b bg-muted/50 text-xs text-muted-foreground">
              <tr>{(["id", "provider", "profile", "state", "activity"] as const).map((c) => <th key={c} scope="col" className="px-4 py-2 text-start font-medium">{t(`columns.${c}`)}</th>)}</tr>
            </thead>
            <tbody className="divide-y">
              {sessions.data.map((s) => (
                <tr key={s.id}>
                  <td className="px-4 py-2"><button type="button" className="min-h-10 text-primary hover:underline" onClick={() => onOpen(s.id)}>#{s.id}</button></td>
                  <td className="px-4 py-2">{s.provider}</td>
                  <td className="px-4 py-2 font-mono text-xs" dir="ltr">{s.profile}</td>
                  <td className="px-4 py-2"><StateLabel state={s.state} />{s.error && <span className="ms-2 text-xs text-muted-foreground" dir="ltr">{s.error}</span>}</td>
                  <td className="px-4 py-2 text-xs">{s.last_activity_at ? fmt.relativeTime(new Date(s.last_activity_at)) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </section>
  );
}

function SessionDialog({ id, manage, onClose }: { id: number; manage: boolean; onClose: () => void }) {
  const t = useTranslations("bridge");
  const tc = useTranslations("common");
  const s = useBridgeSession(id).data;
  const generating = s?.state === "GENERATING" || s?.state === "SENDING";
  const status = useBridgeStatus(id, generating);
  const a = useBridgeActions();
  const fail = useFail();
  const [prompt, setPrompt] = useState("");
  if (!s) return null;
  const canSend = s.state === "READY" || s.state === "COMPLETED";
  const stopped = ["STOPPED", "FAILED", "DISCONNECTED"].includes(s.state);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogTitle className="flex items-center gap-2"><Globe className="size-5" aria-hidden />{t("sessionTitle", { id: s.id, provider: s.provider })}</DialogTitle>
        <DialogDescription className="flex flex-wrap items-center gap-3"><StateLabel state={s.state} /><span className="font-mono text-xs" dir="ltr">{s.profile}</span></DialogDescription>
        {s.error && <p role="alert" className="mt-3 text-sm">{t.has(`errors.${s.error}`) ? t(`errors.${s.error}`) : <span dir="ltr">{s.error}</span>}</p>}
        <ol className="mt-4 flex flex-col gap-2" aria-label={t("history")} data-testid="bb-history">
          {s.history?.map((h, i) => (
            <li key={i} className={cn("rounded-md p-2 text-sm", h.role === "user" ? "bg-muted" : "border")}>
              <p className="text-xs text-muted-foreground">{h.role === "user" ? t("you") : t("assistant")}{h.cancelled && ` · ${t("cancelled")}`}</p>
              <p className="whitespace-pre-wrap" dir="auto">{h.text}</p>
            </li>
          ))}
          {generating && (
            <li className="rounded-md border border-dashed p-2 text-sm" aria-live="polite">
              <p className="text-xs text-muted-foreground">{t("generating")}</p>
              <p className="whitespace-pre-wrap" dir="auto">{status.data?.partial ?? ""}</p>
            </li>
          )}
        </ol>
        {manage && (
          <>
            <form className="mt-4 flex flex-col gap-2" onSubmit={(e) => { e.preventDefault(); a.prompt.mutate({ id, prompt }, { onSuccess: () => setPrompt(""), onError: fail }); }}>
              <Label htmlFor="bb-prompt">{t("prompt")}</Label>
              <Textarea id="bb-prompt" rows={3} value={prompt} onChange={(e) => setPrompt(e.target.value)} dir="auto" disabled={!canSend} />
              <Button type="submit" className="self-start" disabled={!canSend || !prompt.trim() || a.prompt.isPending} data-testid="bb-send"><Send aria-hidden />{t("send")}</Button>
            </form>
            <div className="mt-4 flex flex-wrap gap-2 border-t pt-4">
              {generating && <Button size="sm" variant="outline" onClick={() => a.op.mutate({ id, op: "cancel" }, { onError: fail })}><CircleStop aria-hidden />{t("cancel")}</Button>}
              {s.state === "AUTH_REQUIRED" && <Button size="sm" variant="outline" onClick={() => a.op.mutate({ id, op: "recheck" }, { onError: fail })}><RefreshCw aria-hidden />{t("recheck")}</Button>}
              {canSend && <Button size="sm" variant="outline" onClick={() => a.op.mutate({ id, op: "conversation" }, { onError: fail })}><SquarePlus aria-hidden />{t("newConversation")}</Button>}
              {stopped
                ? <Button size="sm" variant="outline" onClick={() => a.op.mutate({ id, op: "start" }, { onError: fail })}><Play aria-hidden />{t("restart")}</Button>
                : <Button size="sm" variant="outline" onClick={() => a.op.mutate({ id, op: "stop" }, { onError: fail })} data-testid="bb-stop"><Power aria-hidden />{t("stop")}</Button>}
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

const SELECTOR_FIELDS = ["input", "send", "response", "generating", "stop", "login", "new_chat"] as const;

/** When a provider's page changes, the Super Admin adjusts the CSS selectors the bridge looks for (audited). */
function SelectorsDialog({ p, onClose }: { p: BridgeProvider; onClose: () => void }) {
  const t = useTranslations("bridge.selectors");
  const tc = useTranslations("common");
  const a = useBridgeActions();
  const fail = useFail();
  const toast = useToasts((s) => s.push);
  const [v, setV] = useState<Record<string, string>>(() =>
    Object.fromEntries(SELECTOR_FIELDS.map((k) => [k, p.overridden && p.selectors[k] !== p.default_selectors[k] ? p.selectors[k] ?? "" : ""])));
  const save = (selectors: Record<string, string>) => a.selectors.mutate({ provider: p.key, selectors }, {
    onSuccess: () => { toast("success", t("saved")); onClose(); }, onError: fail });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-h-[90vh] max-w-xl overflow-y-auto" data-testid="bb-sel-dialog">
        <DialogTitle>{t("title", { name: p.name })}</DialogTitle>
        <DialogDescription>{t("help")}</DialogDescription>
        <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); save(Object.fromEntries(Object.entries(v).filter(([, x]) => x.trim()))); }}>
          {SELECTOR_FIELDS.map((k) => (
            <div key={k} className="flex flex-col gap-1">
              <Label htmlFor={`sel-${k}`}>{t(`fields.${k}`)}</Label>
              <Input id={`sel-${k}`} dir="ltr" className="font-mono text-xs" maxLength={500} value={v[k]}
                placeholder={p.default_selectors[k] ?? ""} onChange={(e) => setV({ ...v, [k]: e.target.value })} />
            </div>
          ))}
          <p className="text-xs text-muted-foreground">{t("note")}</p>
          <div className="flex flex-wrap justify-end gap-2">
            {p.overridden && <Button type="button" variant="ghost" onClick={() => save({})} data-testid="bb-sel-reset">{t("reset")}</Button>}
            <Button type="button" variant="outline" onClick={onClose}>{t("cancel")}</Button>
            <Button type="submit" disabled={a.selectors.isPending} data-testid="bb-sel-save">{t("save")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** One browser profile per employee, agent or account: its sign-ins stay in it. */
function Profiles({ manage }: { manage: boolean }) {
  const t = useTranslations("bridge.profiles");
  const profiles = useBridgeProfiles();
  const users = useUsers(manage);
  const a = useBridgeActions();
  const fail = useFail();
  const [name, setName] = useState("");
  const [label, setLabel] = useState("");
  const [owner, setOwner] = useState("");
  const ownerName = (o: string | null) => {
    if (!o) return t("account");
    const u = users.data?.find((x) => `user:${x.id}` === o);
    return u ? u.username : o.startsWith("agent:") ? t("agent") : o;
  };
  return (
    <section aria-labelledby="prof-h" className="flex flex-col gap-3" data-testid="bb-profiles">
      <h2 id="prof-h" className="text-lg font-semibold">{t("title")}</h2>
      <p className="max-w-3xl text-sm text-muted-foreground">{t("help")}</p>
      {manage && (
        <Card className="p-4">
          <form className="grid gap-3 sm:grid-cols-[1fr_1fr_1fr_auto] sm:items-end" onSubmit={(e) => {
            e.preventDefault();
            a.addProfile.mutate({ name: name.trim(), label: label.trim(), owner: owner || null }, {
              onSuccess: () => { setName(""); setLabel(""); setOwner(""); }, onError: fail });
          }}>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="pf-name">{t("name")}</Label>
              <Input id="pf-name" required dir="ltr" pattern="[a-z0-9][a-z0-9\-]{0,39}" value={name} onChange={(e) => setName(e.target.value)} aria-describedby="pf-help" />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="pf-label">{t("label")}</Label>
              <Input id="pf-label" value={label} maxLength={80} onChange={(e) => setLabel(e.target.value)} dir="auto" />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="pf-owner">{t("owner")}</Label>
              <NativeSelect id="pf-owner" value={owner} onChange={(e) => setOwner(e.target.value)}>
                <option value="">{t("account")}</option>
                {users.data?.map((u) => <option key={u.id} value={`user:${u.id}`}>{u.username}</option>)}
              </NativeSelect>
            </div>
            <Button type="submit" disabled={a.addProfile.isPending} data-testid="pf-add"><SquarePlus aria-hidden />{t("add")}</Button>
            <p id="pf-help" className="text-xs text-muted-foreground sm:col-span-4">{t("nameHelp")}</p>
          </form>
        </Card>
      )}
      {profiles.data?.length === 0 && <p className="text-sm text-muted-foreground">{t("none")}</p>}
      {!!profiles.data?.length && (
        <Card className="overflow-x-auto" tabIndex={0} aria-label={t("title")}>
          <table className="w-full min-w-[560px] text-sm" data-testid="pf-table">
            <thead className="border-b bg-muted/50 text-xs text-muted-foreground">
              <tr>{(["name", "owner", "status", "actions"] as const).map((c) => <th key={c} scope="col" className="px-4 py-2 text-start font-medium">{t(`columns.${c}`)}</th>)}</tr>
            </thead>
            <tbody className="divide-y">
              {profiles.data.map((p) => (
                <tr key={p.name} data-testid={`pf-${p.name}`}>
                  <td className="px-4 py-2"><span className="font-medium" dir="auto">{p.label || p.name}</span> <span className="font-mono text-xs text-muted-foreground" dir="ltr">{p.name}</span></td>
                  <td className="px-4 py-2" dir="auto">{ownerName(p.owner)}</td>
                  <td className="px-4 py-2">{p.active_session ? t("openIn", { provider: p.active_session.provider }) : t("closed")}</td>
                  <td className="px-4 py-2">
                    {manage && <Button size="sm" variant="ghost" disabled={!!p.active_session || a.removeProfile.isPending}
                      onClick={() => a.removeProfile.mutate(p.name, { onError: fail })} aria-label={`${t("delete")}: ${p.label || p.name}`}>{t("delete")}</Button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </section>
  );
}

/** Any website (Super Admin): stays locked until its terms are acknowledged, like every site. */
function AddSiteDialog({ onClose }: { onClose: () => void }) {
  const t = useTranslations("bridge.sites");
  const tc = useTranslations("common");
  const a = useBridgeActions();
  const fail = useFail();
  const [v, setV] = useState({ key: "", name: "", url: "", terms_url: "", input: "", send: "", response: "" });
  const field = (k: keyof typeof v, opts: { ltr?: boolean; required?: boolean; type?: string; pattern?: string } = {}) => (
    <div className="flex flex-col gap-1">
      <Label htmlFor={`site-${k}`}>{t(`fields.${k}`)}</Label>
      <Input id={`site-${k}`} value={v[k]} required={opts.required} type={opts.type} pattern={opts.pattern} dir={opts.ltr ? "ltr" : "auto"}
        className={opts.ltr ? "font-mono text-xs" : undefined} onChange={(e) => setV({ ...v, [k]: e.target.value })} />
    </div>
  );
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-h-[90vh] max-w-xl overflow-y-auto" data-testid="bb-site-dialog">
        <DialogTitle>{t("title")}</DialogTitle>
        <DialogDescription>{t("help")}</DialogDescription>
        <form className="flex flex-col gap-3" onSubmit={(e) => {
          e.preventDefault();
          const body = Object.fromEntries(Object.entries(v).filter(([, x]) => x.trim()).map(([k, x]) => [k, x.trim()])) as typeof v;
          a.addSite.mutate(body, { onSuccess: onClose, onError: fail });
        }}>
          {field("name", { required: true })}
          {field("key", { ltr: true, required: true, pattern: "[a-z][a-z0-9\\-]{1,31}" })}
          {field("url", { ltr: true, required: true, type: "url" })}
          {field("terms_url", { ltr: true, type: "url" })}
          {field("input", { ltr: true })}
          {field("send", { ltr: true })}
          {field("response", { ltr: true })}
          <p className="text-xs text-muted-foreground">{t("note")}</p>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>{t("cancel")}</Button>
            <Button type="submit" disabled={a.addSite.isPending} data-testid="bb-site-save">{t("save")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
