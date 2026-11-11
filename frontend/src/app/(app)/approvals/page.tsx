"use client";
import { AlertOctagon, AlertTriangle, CheckCircle2, Clock, Info, RotateCcw, ShieldCheck, ShieldQuestion, XCircle } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useCan } from "@/components/providers";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api";
import { type Approval, useApprovals, useDecide } from "@/lib/approvals";
import { useToasts } from "@/lib/toast";
import { cn } from "@/lib/utils";

export default function ApprovalsRoute() {
  return <Suspense><ApprovalsPage /></Suspense>;
}

const RISK = {
  critical: { icon: AlertOctagon, tone: "text-destructive" },
  high: { icon: AlertTriangle, tone: "text-warning" },
  medium: { icon: Info, tone: "text-primary" },
  low: { icon: Info, tone: "text-muted-foreground" },
} as const;

const STATUS_ICON = {
  pending: Clock, approved: CheckCircle2, executed: ShieldCheck, rejected: XCircle,
  changes_requested: RotateCcw, expired: ShieldQuestion,
} as const;

/** An approval raised by the AI Workforce: it accepts one version of one task's result. */
const fromWorkforce = (a: Approval) => a.requested_by === "workforce";
/** Where the task behind an approval can be read: the workforce task card, or the generic task board. */
const taskHref = (a: Approval) => (fromWorkforce(a) ? `/workforce/tasks?task=${a.task_id}` : `/tasks?task=${a.task_id}`);

function opLabel(t: ReturnType<typeof useTranslations<"approvals">>, a: Approval) {
  const key = `operations.${a.operation.replaceAll(".", "_")}`;  // next-intl reads dots as nesting
  return t.has(key) ? t(key) : a.operation_label;
}

/** Risk level: full-contrast text, risk color only on the icon (never color alone). */
function RiskLabel({ risk }: { risk: Approval["risk_level"] }) {
  const t = useTranslations("approvals");
  const r = RISK[risk];
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs font-semibold">
      <r.icon className={cn("size-4", r.tone)} aria-hidden />{t(`risk.${risk}`)}
    </span>
  );
}

function StatusLabel({ status }: { status: Approval["status"] }) {
  const t = useTranslations("approvals");
  const Icon = STATUS_ICON[status];
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium">
      <Icon className="size-3.5" aria-hidden />{t(`status.${status}`)}
    </span>
  );
}

function ApprovalsPage() {
  const t = useTranslations("approvals");
  const tc = useTranslations("common");
  const fmt = useFormatter();
  const sp = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const view = sp.get("view") === "all" ? "all" : "pending";
  const risk = sp.get("risk") ?? "";
  const openId = sp.get("id") ? Number(sp.get("id")) : undefined;
  const set = (patch: Record<string, string | undefined>) => {
    const next = new URLSearchParams(sp.toString());
    for (const [k, v] of Object.entries(patch)) (v ? next.set(k, v) : next.delete(k));
    router.replace(`${pathname}?${next}`, { scroll: false });
  };
  const list = useApprovals({ status: view === "pending" ? "pending" : undefined, risk: risk || undefined });
  const open = list.data?.items.find((a) => a.id === openId);
  const pending = list.data?.counts.pending ?? 0;

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>

      <Card className="flex flex-wrap items-end justify-between gap-3 p-4">
        <div role="group" aria-label={t("filters.view")} className="flex rounded-md border p-0.5">
          {(["pending", "all"] as const).map((v) => (
            <Button key={v} variant={view === v ? "secondary" : "ghost"} size="sm" aria-pressed={view === v}
              onClick={() => set({ view: v === "pending" ? undefined : v })}>
              {v === "pending" ? t("filters.pending", { n: pending }) : t("filters.all")}
            </Button>
          ))}
        </div>
        <div className="flex min-w-44 flex-col gap-1.5">
          <Label htmlFor="f-risk">{t("filters.risk")}</Label>
          <NativeSelect id="f-risk" value={risk} onChange={(e) => set({ risk: e.target.value || undefined })}>
            <option value="">{tc("all")}</option>
            {(["critical", "high", "medium", "low"] as const).map((r) => <option key={r} value={r}>{t(`risk.${r}`)}</option>)}
          </NativeSelect>
        </div>
      </Card>

      {list.isLoading && <div className="flex flex-col gap-3" aria-busy="true">{[0, 1].map((i) => <Skeleton key={i} className="h-24" />)}</div>}
      {list.isError && <Card role="alert" className="p-6 text-center text-destructive">{tc("error")}</Card>}
      {list.data && list.data.items.length === 0 && (
        <Card className="flex flex-col items-center gap-3 p-10 text-center">
          <ShieldCheck className="size-8 text-muted-foreground" aria-hidden />
          <p className="text-muted-foreground">{view === "pending" ? t("emptyPending") : t("empty")}</p>
        </Card>
      )}
      {list.data && list.data.items.length > 0 && (
        <ul className="flex flex-col gap-3" data-testid="approval-list">
          {list.data.items.map((a) => (
            <li key={a.id}>
              <button type="button" onClick={() => set({ id: String(a.id) })} data-testid={`approval-${a.id}`}
                className={cn("flex w-full flex-col gap-2 rounded-lg border bg-card p-4 text-start shadow-sm transition-colors hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  a.status === "pending" && a.risk_level === "critical" && "border-destructive/50")}>
                <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <RiskLabel risk={a.risk_level} />
                  <StatusLabel status={a.status} />
                  <span className="text-xs text-muted-foreground">{opLabel(t, a)}</span>
                </span>
                <span className="font-medium" dir="auto">{a.title}</span>
                <span className="flex flex-wrap gap-x-4 text-xs text-muted-foreground">
                  <span>{t("requestedBy", { who: a.requested_by })}</span>
                  <span>{fmt.relativeTime(new Date(a.created_at))}</span>
                  <span className="font-mono" dir="ltr">#{a.id}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {openId && open && <ApprovalDialog approval={open} onClose={() => set({ id: undefined })} />}
    </div>
  );
}

function ApprovalDialog({ approval: a, onClose }: { approval: Approval; onClose: () => void }) {
  const t = useTranslations("approvals");
  const tc = useTranslations("common");
  const fmt = useFormatter();
  const can = useCan();
  const decide = useDecide();
  const toast = useToasts((s) => s.push);
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState(false);
  const [reviewed, setReviewed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const needsAck = a.risk_level === "critical";

  function submit(decision: "approve" | "reject" | "changes_requested") {
    setError(null);
    if (decision !== "approve" && !reason.trim()) return setReasonError(true);
    decide.mutate({ id: a.id, decision, reason }, {
      onSuccess: (r) => { toast("success", t(`done.${r.status}`)); onClose(); },
      onError: (e) => {
        const code = e instanceof ApiError ? e.code.split(":")[0] : "network";
        setError(t.has(`errors.${code}`) ? t(`errors.${code}`) : tc("requestFailed", { code }));
      },
    });
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <div className="flex flex-col gap-5" data-testid="approval-details">
          <div className="flex flex-col gap-2 pe-8">
            <DialogTitle className="text-lg font-semibold leading-snug" dir="auto">{a.title}</DialogTitle>
            <DialogDescription className="flex flex-wrap items-center gap-3 text-sm">
              <RiskLabel risk={a.risk_level} /><StatusLabel status={a.status} />
              <span className="text-muted-foreground">{opLabel(t, a)}</span>
            </DialogDescription>
          </div>
          {a.summary && <p className="text-sm" dir="auto">{a.summary}</p>}
          {fromWorkforce(a) && a.task_id && (
            <p className="rounded-md border border-s-4 border-s-primary bg-card p-3 text-sm" data-testid="approval-workforce">
              {t("workforceHelp", { version: Number(a.payload.version ?? 0) })}{" "}
              <Link className="font-medium text-primary underline underline-offset-4" href={taskHref(a)}>{t("workforceOpen", { id: a.task_id })}</Link>
            </p>
          )}
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
            {([
              ["requestedBy", a.requested_by],
              ["requestedAt", fmt.dateTime(new Date(a.created_at), { dateStyle: "medium", timeStyle: "short" })],
              ["task", a.task_id ? <Link key="t" className="text-primary underline" href={taskHref(a)} data-testid="approval-task-link">#{a.task_id}</Link> : "—"],
              ["decidedAt", a.decided_at ? fmt.dateTime(new Date(a.decided_at), { dateStyle: "medium", timeStyle: "short" }) : "—"],
              ["executedAt", a.executed_at ? fmt.dateTime(new Date(a.executed_at), { dateStyle: "medium", timeStyle: "short" }) : "—"],
              ["reason", a.reason ?? "—"],
            ] as const).map(([k, v]) => (
              <div key={k} className="flex flex-col"><dt className="text-xs text-muted-foreground">{t(`fields.${k}`)}</dt><dd dir="auto">{v}</dd></div>
            ))}
          </dl>
          <section className="flex flex-col gap-1.5">
            <h3 className="text-sm font-semibold">{t("fields.payload")}</h3>
            <p className="text-xs text-muted-foreground">{a.executable ? t("payloadHelpExecutable") : t("payloadHelp")}</p>
            <pre tabIndex={0} aria-label={t("fields.payload")} className="max-h-48 overflow-auto rounded-md bg-muted p-3 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" dir="ltr">{JSON.stringify(a.payload, null, 2)}</pre>
          </section>
          <section className="flex flex-col gap-1.5">
            <h3 className="text-sm font-semibold">{t("fields.evidence")}</h3>
            <ul className="flex flex-col gap-1 text-xs" data-testid="approval-evidence">
              {a.evidence.map((e, i) => (
                <li key={i} className="rounded-md border p-2">
                  <span className="font-semibold">{t.has(`evidence.${e.kind}`) ? t(`evidence.${e.kind}`) : e.kind}</span>{" "}
                  <span className="break-all font-mono" dir="ltr">{JSON.stringify(Object.fromEntries(Object.entries(e).filter(([k]) => k !== "kind" && k !== "at")))}</span>
                </li>
              ))}
            </ul>
          </section>

          {a.status === "pending" && can("approvals.decide") && (
            <section className="flex flex-col gap-2 border-t pt-4">
              <h3 className="text-sm font-semibold">{t("decision.title")}</h3>
              {error && <p role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">{error}</p>}
              <Label htmlFor="ap-reason">{t("decision.reason")}</Label>
              <Textarea id="ap-reason" rows={3} dir="auto" value={reason} aria-invalid={reasonError || undefined}
                aria-describedby={reasonError ? "ap-reason-error" : "ap-reason-help"}
                onChange={(e) => { setReason(e.target.value); setReasonError(false); }} />
              {reasonError ? <p id="ap-reason-error" className="text-sm text-destructive">{t("decision.reasonRequired")}</p>
                : <p id="ap-reason-help" className="text-xs text-muted-foreground">{t("decision.reasonHelp")}</p>}
              {needsAck && (
                <label className="flex min-h-10 items-center gap-2 text-sm">
                  <input type="checkbox" className="size-4 accent-[var(--primary)]" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} data-testid="ap-ack" />
                  {t("decision.ack")}
                </label>
              )}
              <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
                <Button variant="destructive" disabled={decide.isPending} onClick={() => submit("reject")}><XCircle aria-hidden />{t("decision.reject")}</Button>
                <div className="flex flex-wrap gap-2">
                  <Button variant="outline" disabled={decide.isPending} onClick={() => submit("changes_requested")}><RotateCcw aria-hidden />{t("decision.changes")}</Button>
                  <Button disabled={decide.isPending || (needsAck && !reviewed)} onClick={() => submit("approve")} data-testid="ap-approve">
                    <CheckCircle2 aria-hidden />{t("decision.approve")}
                  </Button>
                </div>
              </div>
            </section>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
