"use client";
import { Activity, KeyRound, Plus, Puzzle, Search, Server, Trash2, UserPlus } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { AssessmentView, RiskLabel } from "@/components/assessment-view";
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
import { useAgents } from "@/lib/agents";
import { ApiError } from "@/lib/api";
import { type Assessment, type Inspection, type Skill, useIntegrationActions, useLocalSkills, useMcpKeys, useServer, useServers, useSkills } from "@/lib/integrations";
import { useToasts } from "@/lib/toast";
import { cn } from "@/lib/utils";

const TABS = ["servers", "skills", "keys"] as const;
type Tab = (typeof TABS)[number];

function useFail() {
  const tc = useTranslations("common");
  const toast = useToasts((s) => s.push);
  return (e: unknown) => toast("error", tc("requestFailed", { code: e instanceof ApiError ? e.code : "error" }));
}

export default function IntegrationsPage() {
  const t = useTranslations("integrations");
  const can = useCan();
  const [tab, setTab] = useState<Tab>("servers");
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>
      <div role="tablist" aria-label={t("title")} className="flex flex-wrap gap-2 border-b pb-2">
        {TABS.filter((x) => x !== "keys" || can("skills.manage")).map((x) => (
          <Button key={x} role="tab" id={`tab-${x}`} aria-selected={tab === x} aria-controls={`panel-${x}`}
            variant={tab === x ? "default" : "ghost"} onClick={() => setTab(x)} data-testid={`tab-${x}`}>
            {x === "servers" ? <Server aria-hidden /> : x === "skills" ? <Puzzle aria-hidden /> : <KeyRound aria-hidden />}{t(`tabs.${x}`)}
          </Button>
        ))}
      </div>
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === "servers" && <Servers />}
        {tab === "skills" && <Skills />}
        {tab === "keys" && <Keys />}
      </div>
    </div>
  );
}

/** Enabling something risky needs an explicit acceptance (the backend enforces it too). */
function EnableDialog({ name, assessment, onClose, onConfirm, busy }: {
  name: string; assessment: Assessment | null; onClose: () => void; onConfirm: (accept: boolean) => void; busy: boolean;
}) {
  const t = useTranslations("integrations");
  const tc = useTranslations("common");
  const [ok, setOk] = useState(false);
  const high = assessment?.risk === "high";
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogTitle>{t("enableTitle", { name })}</DialogTitle>
        <DialogDescription className="flex flex-wrap items-center gap-2">{t("enableHelp")} <RiskLabel risk={assessment?.risk} /></DialogDescription>
        {assessment && <div className="mt-4"><AssessmentView a={assessment} /></div>}
        {high && (
          <label className="mt-4 flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1 size-4" checked={ok} onChange={(e) => setOk(e.target.checked)} data-testid="accept-risks" />
            <span>{t("acceptRisks")}</span>
          </label>
        )}
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>{tc("cancel")}</Button>
          <Button disabled={busy || (high && !ok)} onClick={() => onConfirm(high && ok)} data-testid="confirm-enable">{t("enable")}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function StatusDot({ status }: { status: string }) {
  const t = useTranslations("integrations");
  return (
    <span className="inline-flex items-center gap-1.5 text-xs">
      <span aria-hidden className={cn("size-2 rounded-full", status === "ok" ? "bg-success" : status === "error" ? "bg-destructive" : "bg-muted-foreground")} />
      {t.has(`status.${status}`) ? t(`status.${status}`) : status}
    </span>
  );
}

// ---------------- MCP servers ----------------
function Servers() {
  const t = useTranslations("integrations");
  const can = useCan();
  const servers = useServers();
  const [open, setOpen] = useState<number>();
  const [adding, setAdding] = useState(false);
  const manage = can("skills.manage");
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">{t("serversHelp")}</p>
        {manage && <Button onClick={() => setAdding(true)} data-testid="mcp-add"><Plus aria-hidden />{t("register")}</Button>}
      </div>
      {servers.isLoading && <Skeleton className="h-32" aria-busy="true" />}
      {servers.data?.length === 0 && <Card className="p-8 text-center text-muted-foreground">{t("noServers")}</Card>}
      <ul className="grid gap-3 md:grid-cols-2">
        {servers.data?.map((s) => (
          <li key={s.id}>
            <Card className="flex h-full flex-col gap-2 p-4" data-testid={`mcp-${s.name}`}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <h2 className="font-mono text-base font-semibold" dir="ltr">{s.name}</h2>
                <RiskLabel risk={s.assessment?.risk} />
              </div>
              <p className="truncate font-mono text-xs text-muted-foreground" dir="ltr" title={s.url ?? s.command?.join(" ")}>{s.transport} · {s.url ?? s.command?.join(" ")}</p>
              <div className="flex flex-wrap items-center gap-3 text-xs">
                <StatusDot status={s.status} />
                <span>{s.enabled ? t("enabled") : t("disabled")}</span>
              </div>
              <Button variant="outline" size="sm" className="mt-auto self-start" onClick={() => setOpen(s.id)} aria-label={`${t("manage")}: ${s.name}`}>{t("manage")}</Button>
            </Card>
          </li>
        ))}
      </ul>
      {adding && <RegisterDialog onClose={() => setAdding(false)} />}
      {open && <ServerDialog id={open} onClose={() => setOpen(undefined)} />}
    </div>
  );
}

function RegisterDialog({ onClose }: { onClose: () => void }) {
  const t = useTranslations("integrations");
  const tc = useTranslations("common");
  const a = useIntegrationActions();
  const fail = useFail();
  const [name, setName] = useState("");
  const [transport, setTransport] = useState("http");
  const [command, setCommand] = useState("");
  const [url, setUrl] = useState("");
  const [token, setToken] = useState("");
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-w-lg">
        <DialogTitle>{t("register")}</DialogTitle>
        <DialogDescription>{t("registerHelp")}</DialogDescription>
        <form className="mt-4 flex flex-col gap-4" onSubmit={(e) => {
          e.preventDefault();
          a.register.mutate({ name: name.trim(), transport, ...(transport === "stdio" ? { command: command.split("\n").map((x) => x.trim()).filter(Boolean) } : { url: url.trim(), token: token || undefined }) },
            { onSuccess: onClose, onError: fail });
        }}>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="r-name">{t("name")}</Label>
            <Input id="r-name" required pattern="[a-z0-9][a-z0-9\-]{0,62}" value={name} onChange={(e) => setName(e.target.value)} dir="ltr" aria-describedby="r-name-help" />
            <p id="r-name-help" className="text-xs text-muted-foreground">{t("nameHelp")}</p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="r-transport">{t("transport")}</Label>
            <NativeSelect id="r-transport" value={transport} onChange={(e) => setTransport(e.target.value)}>
              <option value="http">{t("transports.http")}</option>
              <option value="stdio">{t("transports.stdio")}</option>
            </NativeSelect>
          </div>
          {transport === "stdio" ? (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="r-cmd">{t("command")}</Label>
              <Textarea id="r-cmd" required rows={3} value={command} onChange={(e) => setCommand(e.target.value)} dir="ltr" aria-describedby="r-cmd-help" />
              <p id="r-cmd-help" className="text-xs text-muted-foreground">{t("commandHelp")}</p>
            </div>
          ) : (
            <>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="r-url">{t("url")}</Label>
                <Input id="r-url" required type="url" value={url} onChange={(e) => setUrl(e.target.value)} dir="ltr" />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="r-token">{t("token")} <span className="text-muted-foreground">({tc("optional")})</span></Label>
                <Input id="r-token" type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} dir="ltr" />
              </div>
            </>
          )}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>{tc("cancel")}</Button>
            <Button type="submit" disabled={a.register.isPending} data-testid="r-submit">{t("registerSubmit")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ServerDialog({ id, onClose }: { id: number; onClose: () => void }) {
  const t = useTranslations("integrations");
  const tc = useTranslations("common");
  const can = useCan();
  const s = useServer(id).data;
  const a = useIntegrationActions();
  const fail = useFail();
  const toast = useToasts((x) => x.push);
  const [enabling, setEnabling] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const manage = can("skills.manage");
  if (!s) return null;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogTitle className="font-mono" dir="ltr">{s.name}</DialogTitle>
        <DialogDescription className="flex flex-wrap items-center gap-3"><RiskLabel risk={s.assessment?.risk} /><StatusDot status={s.status} /><span>{s.enabled ? t("enabled") : t("disabled")}</span></DialogDescription>
        {s.last_error && <p role="alert" className="mt-3 text-sm text-destructive" dir="auto">{s.last_error}</p>}
        {manage && (
          <div className="mt-4 flex flex-wrap gap-2">
            <Button size="sm" variant="outline" disabled={a.discover.isPending} onClick={() => a.discover.mutate(s.id, { onError: fail })} data-testid="mcp-discover"><Search aria-hidden />{t("discover")}</Button>
            <Button size="sm" variant="outline" disabled={a.health.isPending} onClick={() => a.health.mutate(s.id, { onSuccess: (h) => toast("success", t("healthy", { ms: h.latency_ms })), onError: fail })}><Activity aria-hidden />{t("checkHealth")}</Button>
            {s.enabled
              ? <Button size="sm" variant="outline" onClick={() => a.enableServer.mutate({ id: s.id, enabled: false }, { onError: fail })}>{t("disable")}</Button>
              : <Button size="sm" onClick={() => setEnabling(true)} data-testid="mcp-enable">{t("enable")}</Button>}
            <Button size="sm" variant="outline" onClick={() => setDeleting(true)}><Trash2 aria-hidden />{t("delete")}</Button>
          </div>
        )}
        <section className="mt-6" aria-labelledby="tools-h">
          <h3 id="tools-h" className="text-sm font-semibold">{t("tools")}</h3>
          {!s.tools?.length ? <p className="mt-1 text-sm text-muted-foreground">{t("noTools")}</p> : (
            <ul className="mt-2 flex flex-col divide-y rounded-md border" data-testid="mcp-tools">
              {s.tools.map((tool) => (
                <li key={tool.id} className="flex items-start gap-3 p-2">
                  <input type="checkbox" id={`tool-${tool.id}`} className="mt-1 size-4" checked={tool.enabled} disabled={!manage}
                    onChange={(e) => a.enableTool.mutate({ server: s.id, tool: tool.id, enabled: e.target.checked }, { onError: fail })} />
                  <label htmlFor={`tool-${tool.id}`} className="flex min-w-0 flex-col text-sm">
                    <span className="font-mono" dir="ltr">{tool.name}</span>
                    <span className="text-xs text-muted-foreground" dir="auto">{tool.description}</span>
                    <span className="font-mono text-xs text-muted-foreground" dir="ltr">{tool.permission_key}</span>
                  </label>
                </li>
              ))}
            </ul>
          )}
          {s.resources.length > 0 && <p className="mt-2 text-xs text-muted-foreground">{t("resources", { n: s.resources.length })}</p>}
        </section>
        {s.assessment && (
          <section className="mt-6" aria-labelledby="assess-h">
            <h3 id="assess-h" className="mb-2 text-sm font-semibold">{t("assessment")}</h3>
            <AssessmentView a={s.assessment} />
          </section>
        )}
        {enabling && <EnableDialog name={s.name} assessment={s.assessment} busy={a.enableServer.isPending} onClose={() => setEnabling(false)}
          onConfirm={(accept) => a.enableServer.mutate({ id: s.id, enabled: true, accept_risks: accept }, { onSuccess: () => setEnabling(false), onError: fail })} />}
        <ConfirmDialog open={deleting} onOpenChange={setDeleting} destructive title={t("deleteServer")} description={t("deleteServerBody", { name: s.name })}
          confirmLabel={t("delete")} busy={a.deleteServer.isPending} onConfirm={() => a.deleteServer.mutate(s.id, { onSuccess: onClose, onError: fail })} />
      </DialogContent>
    </Dialog>
  );
}

// ---------------- skills ----------------
function Skills() {
  const t = useTranslations("integrations");
  const can = useCan();
  const manage = can("skills.manage");
  const skills = useSkills();
  const local = useLocalSkills(manage);
  const a = useIntegrationActions();
  const fail = useFail();
  const [inspection, setInspection] = useState<Inspection & { local: string }>();
  const [enabling, setEnabling] = useState<Skill>();
  const [assigning, setAssigning] = useState<Skill>();
  const [deleting, setDeleting] = useState<Skill>();
  const installed = new Set(skills.data?.map((k) => k.source));
  return (
    <div className="flex flex-col gap-6">
      {manage && (
        <section aria-labelledby="local-h" className="flex flex-col gap-2">
          <h2 id="local-h" className="text-lg font-semibold">{t("availableSkills")}</h2>
          <p className="text-sm text-muted-foreground">{t("availableHelp")}</p>
          {local.data?.length === 0 && <p className="text-sm text-muted-foreground">{t("noLocalSkills")}</p>}
          <ul className="grid gap-2 md:grid-cols-2">
            {local.data?.map((l) => (
              <li key={l.local}>
                <Card className="flex items-center justify-between gap-3 p-3">
                  <div className="min-w-0">
                    <p className="font-medium" dir="auto">{l.name}</p>
                    <p className="line-clamp-2 text-xs text-muted-foreground" dir="auto">{l.description}</p>
                  </div>
                  {installed.has(`local:${l.local}`) ? <span className="text-xs text-muted-foreground">{t("installed")}</span> : (
                    <Button size="sm" variant="outline" disabled={a.inspect.isPending} aria-label={`${t("inspect")}: ${l.name}`}
                      onClick={() => a.inspect.mutate(l.local, { onSuccess: (r) => setInspection({ ...r, local: l.local }), onError: fail })}>
                      <Search aria-hidden />{t("inspect")}
                    </Button>
                  )}
                </Card>
              </li>
            ))}
          </ul>
        </section>
      )}
      <section aria-labelledby="installed-h" className="flex flex-col gap-2">
        <h2 id="installed-h" className="text-lg font-semibold">{t("installedSkills")}</h2>
        {skills.data?.length === 0 && <p className="text-sm text-muted-foreground">{t("noSkills")}</p>}
        <ul className="flex flex-col gap-3">
          {skills.data?.map((k) => (
            <li key={k.id}>
              <Card className="flex flex-col gap-2 p-4" data-testid={`skill-${k.key}`}>
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <h3 className="font-semibold" dir="auto">{k.name}</h3>
                    <p className="text-xs text-muted-foreground"><span dir="ltr">{k.source}</span>{k.license && <> · {k.license}</>}{k.version && <> · v{k.version}</>}</p>
                  </div>
                  <RiskLabel risk={k.assessment?.risk} />
                </div>
                <p className="line-clamp-3 whitespace-pre-wrap text-sm" dir="auto">{k.instructions}</p>
                <p className="text-xs">{k.enabled ? t("enabled") : t("disabled")} · {t("assignedTo", { n: k.agent_ids.length })}</p>
                {manage && (
                  <div className="flex flex-wrap gap-2">
                    {k.enabled
                      ? <Button size="sm" variant="outline" onClick={() => a.enableSkill.mutate({ id: k.id, enabled: false }, { onError: fail })}>{t("disable")}</Button>
                      : <Button size="sm" onClick={() => setEnabling(k)} aria-label={`${t("enable")}: ${k.name}`}>{t("enable")}</Button>}
                    <Button size="sm" variant="outline" onClick={() => setAssigning(k)} aria-label={`${t("assign")}: ${k.name}`}><UserPlus aria-hidden />{t("assign")}</Button>
                    <Button size="sm" variant="outline" onClick={() => setDeleting(k)} aria-label={`${t("delete")}: ${k.name}`}><Trash2 aria-hidden />{t("delete")}</Button>
                  </div>
                )}
              </Card>
            </li>
          ))}
        </ul>
      </section>
      {inspection && <InspectDialog r={inspection} onClose={() => setInspection(undefined)} />}
      {enabling && <EnableDialog name={enabling.name} assessment={enabling.assessment} busy={a.enableSkill.isPending} onClose={() => setEnabling(undefined)}
        onConfirm={(accept) => a.enableSkill.mutate({ id: enabling.id, enabled: true, accept_risks: accept }, { onSuccess: () => setEnabling(undefined), onError: fail })} />}
      {assigning && <AssignDialog skill={assigning} onClose={() => setAssigning(undefined)} />}
      <ConfirmDialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(undefined)} destructive title={t("deleteSkill")} description={t("deleteSkillBody", { name: deleting?.name ?? "" })}
        confirmLabel={t("delete")} busy={a.deleteSkill.isPending} onConfirm={() => deleting && a.deleteSkill.mutate(deleting.id, { onSuccess: () => setDeleting(undefined), onError: fail })} />
    </div>
  );
}

function InspectDialog({ r, onClose }: { r: Inspection & { local: string }; onClose: () => void }) {
  const t = useTranslations("integrations");
  const tc = useTranslations("common");
  const a = useIntegrationActions();
  const fail = useFail();
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogTitle dir="auto">{r.name ?? r.local}</DialogTitle>
        <DialogDescription className="flex flex-wrap items-center gap-2">{t("inspectHelp")} <RiskLabel risk={r.assessment.risk} /></DialogDescription>
        <p className="mt-3 text-sm" dir="auto">{r.description}</p>
        <p className="mt-1 text-xs text-muted-foreground">{t("filesScanned", { n: r.files.length })}{r.license && <> · {r.license}</>}</p>
        <div className="mt-4"><AssessmentView a={r.assessment} /></div>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>{tc("cancel")}</Button>
          <Button disabled={a.install.isPending} onClick={() => a.install.mutate(r.local, { onSuccess: onClose, onError: fail })} data-testid="skill-install">{t("installDisabled")}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function AssignDialog({ skill, onClose }: { skill: Skill; onClose: () => void }) {
  const t = useTranslations("integrations");
  const tc = useTranslations("common");
  const agents = useAgents({});
  const a = useIntegrationActions();
  const fail = useFail();
  const [ids, setIds] = useState<number[]>(skill.agent_ids);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-h-[90vh] max-w-lg overflow-y-auto">
        <DialogTitle>{t("assignTitle", { name: skill.name })}</DialogTitle>
        <DialogDescription>{t("assignHelp")}</DialogDescription>
        <fieldset className="mt-4 flex flex-col gap-2">
          <legend className="sr-only">{t("assign")}</legend>
          {agents.data?.map((ag) => (
            <label key={ag.id} className="flex items-center gap-2 text-sm">
              <input type="checkbox" className="size-4" checked={ids.includes(ag.id)} onChange={(e) => setIds(e.target.checked ? [...ids, ag.id] : ids.filter((x) => x !== ag.id))} />
              <span dir="auto">{ag.name}</span>
            </label>
          ))}
        </fieldset>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>{tc("cancel")}</Button>
          <Button disabled={a.assignSkill.isPending} onClick={() => a.assignSkill.mutate({ id: skill.id, agent_ids: ids }, { onSuccess: onClose, onError: fail })}>{tc("save")}</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// ---------------- access keys for the platform MCP server ----------------
function Keys() {
  const t = useTranslations("integrations");
  const tc = useTranslations("common");
  const can = useCan();
  const keys = useMcpKeys(true);
  const a = useIntegrationActions();
  const fail = useFail();
  const [name, setName] = useState("");
  const [tools, setTools] = useState<string[]>([]);
  const [token, setToken] = useState<string>();
  const browserOk = can("browser_bridge.manage");
  const d = keys.data;
  return (
    <div className="flex flex-col gap-6">
      <Card className="flex flex-col gap-4 p-4">
        <div>
          <h2 className="text-lg font-semibold">{t("newKey")}</h2>
          <p className="text-sm text-muted-foreground">{t("keysHelp")} <code className="font-mono" dir="ltr">{d?.endpoint}</code></p>
        </div>
        <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); a.createKey.mutate({ name: name.trim(), tools }, { onSuccess: (k) => { setToken(k.token); setName(""); setTools([]); }, onError: fail }); }}>
          <div className="flex max-w-sm flex-col gap-1.5">
            <Label htmlFor="k-name">{t("name")}</Label>
            <Input id="k-name" required maxLength={128} value={name} onChange={(e) => setName(e.target.value)} dir="auto" />
          </div>
          <fieldset className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            <legend className="mb-1 text-sm font-medium">{t("keyTools")}</legend>
            {d?.tools.map((x) => {
              const browser = d.browser_tools.includes(x);
              return (
                <label key={x} className={cn("flex items-center gap-2 text-sm", browser && !browserOk && "opacity-60")}>
                  <input type="checkbox" className="size-4" disabled={browser && !browserOk} checked={tools.includes(x)} onChange={(e) => setTools(e.target.checked ? [...tools, x] : tools.filter((y) => y !== x))} />
                  <span className="font-mono text-xs" dir="ltr">{x}</span>{browser && <span className="text-xs text-muted-foreground">({t("experimental")})</span>}
                </label>
              );
            })}
          </fieldset>
          <Button type="submit" className="self-start" disabled={!tools.length || a.createKey.isPending} data-testid="key-create"><KeyRound aria-hidden />{t("createKey")}</Button>
        </form>
        {token && (
          <div role="status" className="rounded-md border border-warning p-3 text-sm">
            <p className="font-medium">{t("tokenOnce")}</p>
            <code className="mt-1 block break-all font-mono text-xs" dir="ltr" data-testid="key-token">{token}</code>
          </div>
        )}
      </Card>
      <ul className="flex flex-col gap-2" aria-label={t("tabs.keys")}>
        {d?.keys.map((k) => (
          <li key={k.id}>
            <Card className="flex flex-wrap items-center justify-between gap-2 p-3">
              <div className="min-w-0">
                <p className="font-medium" dir="auto">{k.name} <span className="font-mono text-xs text-muted-foreground" dir="ltr">…{k.hint}</span></p>
                <p className="font-mono text-xs text-muted-foreground" dir="ltr">{k.tools.join(", ")}</p>
              </div>
              {k.revoked_at ? <span className="text-xs text-muted-foreground">{t("revoked")}</span>
                : <Button size="sm" variant="outline" onClick={() => a.revokeKey.mutate(k.id, { onError: fail })} aria-label={`${t("revoke")}: ${k.name}`}>{t("revoke")}</Button>}
            </Card>
          </li>
        ))}
      </ul>
      {d && d.keys.length === 0 && <p className="text-sm text-muted-foreground">{tc("empty")}</p>}
    </div>
  );
}

