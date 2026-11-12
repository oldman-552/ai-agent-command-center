"use client";
import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, ChevronDown, LogIn, MonitorSmartphone, Power, ShieldCheck, Wand2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { useCan } from "@/components/providers";
import { Button } from "@/components/ui/button";
import { useBridgeActions } from "@/lib/browser-bridge";
import { useToasts } from "@/lib/toast";
import { type Readiness, useReadiness, useWorkforceActions } from "@/lib/workforce";
import { useFail } from "./shared";

/**
 * ADR-037: "is the team ready?" — before a goal is sent, every missing piece (Browser Bridge off or not running, the
 * site's terms, a member without its own window, a window that needs a sign-in) with the fix next to it, and one
 * button that moves the whole team to its own windows. Nothing here types into a window or sends anything to a site.
 */
export function TeamReadiness({ compact = false }: { compact?: boolean }) {
  const t = useTranslations("workforce.readiness");
  const can = useCan();
  const manage = can("workforce.manage") && can("browser_bridge.manage");
  const q = useReadiness(can("workforce.read"));
  const r = q.data;
  const [open, setOpen] = useState(false);
  if (!r) return null;
  const problems = r.members.filter((m) => m.problem);
  if (r.ready) {
    return (
      <div role="status" data-testid="wf-readiness" data-ready="true"
        className="flex flex-wrap items-center gap-2 rounded-lg border border-emerald-600/40 bg-emerald-600/5 px-4 py-2 text-sm">
        <CheckCircle2 className="size-4 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden />
        <span className="font-medium">{t("ready")}</span>
        <span className="text-muted-foreground">{t("readyHelp", { n: r.members.filter((m) => m.mode !== "DISABLED").length })}</span>
      </div>
    );
  }
  const missing = [!r.checks.bridge_enabled, !r.checks.bridge_running, !r.checks.terms].filter(Boolean).length + problems.length;
  return (
    <section aria-labelledby="wf-ready-h" data-testid="wf-readiness" data-ready="false"
      className="flex flex-col gap-3 rounded-lg border border-amber-500/50 bg-amber-500/5 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <AlertTriangle className="size-5 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden />
        <h2 id="wf-ready-h" className="min-w-0 flex-1 text-base font-semibold">{t("notReady", { n: missing })}</h2>
        {manage && <SetupAll r={r} />}
        {compact && (
          <Button variant="ghost" size="sm" aria-expanded={open} onClick={() => setOpen(!open)} data-testid="wf-readiness-toggle">
            {open ? t("hide") : t("details")}<ChevronDown aria-hidden className={open ? "rotate-180 transition-transform" : "transition-transform"} />
          </Button>
        )}
      </div>
      {(!compact || open) && <Steps r={r} manage={manage} />}
    </section>
  );
}

function SetupAll({ r }: { r: Readiness }) {
  const t = useTranslations("workforce.readiness");
  const a = useWorkforceActions();
  const fail = useFail();
  const toast = useToasts((s) => s.push);
  const blocked = !r.checks.bridge_enabled || !r.checks.bridge_running || !r.checks.terms;
  return (
    <Button onClick={() => a.openAllWindows.mutate(undefined, {
      onError: fail,
      onSuccess: (out) => {
        const bad = out.results.filter((x) => !x.ok);
        toast(bad.length ? "error" : "success", bad.length
          ? t("setupPartly", { list: bad.map((x) => `${x.label}: ${x.error}`).join(" · ") })
          : t("setupDone", { n: out.results.length }));
      } })}
      disabled={blocked || a.openAllWindows.isPending} title={blocked ? t("setupBlocked") : undefined} data-testid="wf-readiness-setup-all">
      <Wand2 aria-hidden />{a.openAllWindows.isPending ? t("settingUp") : t("setupAll")}
    </Button>
  );
}

function Steps({ r, manage }: { r: Readiness; manage: boolean }) {
  const t = useTranslations("workforce.readiness");
  const qc = useQueryClient();
  const b = useBridgeActions();
  const a = useWorkforceActions();
  const fail = useFail();
  const toast = useToasts((s) => s.push);
  const refresh = () => ["wf-readiness", "wf"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const openFor = (m: Readiness["members"][number]) => {
    const done = { onError: fail, onSuccess: () => { toast("success", t("windowOpened", { label: m.label })); refresh(); } };
    if (m.kind === "manager") a.openManagerBrowser.mutate({ site: r.site }, done);
    else if (m.worker_id) a.openWorkerBrowser.mutate({ id: m.worker_id, site: r.site }, done);
  };
  const busy = a.openManagerBrowser.isPending || a.openWorkerBrowser.isPending;
  const canWindows = r.checks.bridge_enabled && r.checks.bridge_running && r.checks.terms;
  return (
    <ol className="flex flex-col gap-2 text-sm">
      <Step ok={r.checks.bridge_running} icon={Power} title={t("running")} help={t("runningHelp")} testid="running">
        {!r.checks.bridge_running && <code className="rounded bg-muted px-2 py-1 text-xs" dir="ltr">{t("startCmd")}</code>}
      </Step>
      <Step ok={r.checks.bridge_enabled} icon={MonitorSmartphone} title={t("enabled")} help={t("enabledHelp")} testid="enabled">
        {!r.checks.bridge_enabled && manage && (
          <Button size="sm" onClick={() => b.setEnabled.mutate(true, { onError: fail, onSuccess: refresh })} disabled={b.setEnabled.isPending}
            data-testid="wf-readiness-enable">{t("enable")}</Button>
        )}
      </Step>
      <Step ok={r.checks.terms} icon={ShieldCheck} title={t("terms")} help={t("termsHelp")} testid="terms">
        {!r.checks.terms && r.terms.statement && (
          <div className="flex w-full flex-col gap-2">
            <blockquote className="rounded-md border bg-background p-3 text-xs leading-relaxed" dir="ltr" data-testid="wf-readiness-statement">{r.terms.statement}</blockquote>
            {manage && (
              <div>
                <Button size="sm" variant="outline" disabled={b.acknowledge.isPending} data-testid="wf-readiness-ack"
                  onClick={() => b.acknowledge.mutate({ provider: r.terms.site, statement: r.terms.statement! }, { onError: fail, onSuccess: refresh })}>
                  {t("acknowledge")}</Button>
              </div>
            )}
          </div>
        )}
      </Step>
      {r.members.filter((m) => m.problem).map((m) => (
        <Step key={m.label} ok={false} icon={m.problem === "sign_in" ? LogIn : MonitorSmartphone} testid={`member-${m.label}`}
          title={`${m.kind === "manager" ? t("manager") : m.label} · ${m.name}`} help={t(`problem.${m.problem!}`)}>
          {manage && (
            <Button size="sm" variant="outline" onClick={() => openFor(m)} disabled={busy || !canWindows}
              data-testid={`wf-readiness-open-${m.label}`}>
              {m.problem === "sign_in" ? t("signedIn") : t("openWindow")}
            </Button>
          )}
        </Step>
      ))}
    </ol>
  );
}

function Step({ ok, icon: Icon, title, help, testid, children }: {
  ok: boolean; icon: typeof Power; title: string; help: string; testid: string; children?: React.ReactNode;
}) {
  const t = useTranslations("workforce.readiness");
  return (
    <li className="flex flex-wrap items-start gap-3 rounded-md border bg-card p-3" data-testid={`wf-readiness-${testid}`} data-ok={ok}>
      {ok ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden />
        : <Icon className="mt-0.5 size-4 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden />}
      <div className="min-w-0 flex-1">
        <p className="font-medium">{title} <span className="sr-only">{ok ? t("okSr") : t("missingSr")}</span></p>
        {!ok && <p className="text-muted-foreground">{help}</p>}
      </div>
      {children}
    </li>
  );
}
