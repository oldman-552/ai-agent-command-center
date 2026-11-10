"use client";
import { Activity, AlertTriangle, CircleCheck, CircleHelp, Clock, FileText, Wrench } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useCan } from "@/components/providers";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ApiError } from "@/lib/api";
import { useToasts } from "@/lib/toast";
import { NativeSelect } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { type ExecEvent, useAnswerInput, useExecEvents, useExecution, useExecutions } from "@/lib/admin";
import { useAgents } from "@/lib/agents";
import { cn } from "@/lib/utils";

const STATUSES = ["CREATED", "INITIALIZING", "READY", "RUNNING", "WAITING_FOR_TOOL", "WAITING_FOR_APPROVAL", "WAITING_FOR_INPUT", "CHECKPOINTED", "RECOVERING", "COMPLETED", "FAILED", "CANCELLED", "TERMINATED"];
const LIVE = new Set(["CREATED", "INITIALIZING", "READY", "RUNNING", "WAITING_FOR_TOOL", "WAITING_FOR_APPROVAL", "WAITING_FOR_INPUT", "RECOVERING"]);
const TONE: Record<string, string> = { COMPLETED: "bg-success", FAILED: "bg-destructive", TERMINATED: "bg-destructive", CANCELLED: "bg-muted-foreground", RUNNING: "bg-primary" };

function Dot({ status }: { status: string }) {
  return <span aria-hidden className={cn("inline-block size-2 rounded-full", TONE[status] ?? "bg-warning")} />;
}

export default function Page() {
  return <Suspense><ExecutionsPage /></Suspense>;
}

function ExecutionsPage() {
  const t = useTranslations("executions");
  const sp = useSearchParams();
  const router = useRouter();
  const status = sp.get("status") ?? "";
  const selected = sp.get("id") ?? undefined;
  const list = useExecutions(status || undefined);
  const agents = useAgents({});
  const fmt = useFormatter();
  const name = (id: number | null) => (id ? agents.data?.find((a) => a.id === id)?.name ?? `#${id}` : "—");
  const set = (k: string, v?: string) => {
    const n = new URLSearchParams(sp.toString());
    if (v) n.set(k, v); else n.delete(k);
    router.replace(`/executions?${n}`);
  };
  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>
      <div className="flex max-w-xs flex-col gap-1.5">
        <Label htmlFor="ex-status">{t("status")}</Label>
        <NativeSelect id="ex-status" value={status} onChange={(e) => set("status", e.target.value || undefined)}>
          <option value="">{t("all")}</option>
          {STATUSES.map((s) => <option key={s} value={s}>{t(`statuses.${s}`)}</option>)}
        </NativeSelect>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <Card className="overflow-hidden">
          {list.isLoading && <Skeleton className="m-4 h-40" aria-busy="true" />}
          {list.data?.length === 0 && <p className="p-6 text-center text-sm text-muted-foreground">{t("empty")}</p>}
          <ul className="max-h-[70vh] divide-y overflow-y-auto" aria-label={t("title")} data-testid="ex-list">
            {list.data?.map((x) => (
              <li key={x.id}>
                <button type="button" onClick={() => set("id", x.id)} aria-current={selected === x.id ? "true" : undefined}
                  className={cn("flex w-full flex-col gap-1 px-4 py-3 text-start hover:bg-muted/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", selected === x.id && "bg-muted")}>
                  <span className="flex items-center gap-2 text-sm font-medium"><Dot status={x.status} />{t(`statuses.${x.status}`)}<span className="font-mono text-xs text-muted-foreground" dir="ltr">{x.id.slice(0, 8)}</span>{x.is_mock && <span className="text-xs text-muted-foreground">{t("mock")}</span>}</span>
                  <span className="text-xs text-muted-foreground"><span dir="auto">{name(x.agent_id)}</span> · {x.engine}{x.model ? ` · ${x.model}` : ""} · {fmt.relativeTime(new Date(x.created_at))}</span>
                </button>
              </li>
            ))}
          </ul>
        </Card>
        {selected ? <Timeline id={selected} /> : <Card className="flex items-center justify-center p-10 text-sm text-muted-foreground">{t("pick")}</Card>}
      </div>
    </div>
  );
}

const ICON: Record<string, typeof Activity> = { state: Activity, tool_call: Wrench, tool_result: Wrench, log: FileText, error: AlertTriangle, output_delta: FileText, usage: CircleCheck, checkpoint: Clock, input_required: CircleHelp, input_answered: CircleCheck };

function summarize(e: ExecEvent): string {
  const p = e.payload as Record<string, unknown>;
  if (e.type === "state") return `${p.from ?? ""} → ${p.to}${p.reason ? ` — ${p.reason}` : ""}`;
  if (e.type === "tool_call") return `${p.tool}`;
  if (e.type === "tool_result") return `${p.tool}: ${JSON.stringify(p.result ?? {}).slice(0, 160)}`;
  if (e.type === "usage") return `${p.input_tokens ?? 0} in / ${p.output_tokens ?? 0} out${p.cost_usd ? ` · $${p.cost_usd}` : ""}`;
  if (e.type === "log" || e.type === "error") return String(p.message ?? JSON.stringify(p)).slice(0, 300);
  if (e.type === "input_required") return String(p.question ?? "");
  if (e.type === "input_answered") return String(p.by ?? "");
  return JSON.stringify(p).slice(0, 200);
}

/** UX-03: everything that happened in one execution, in order, with live updates while it runs. */
function Timeline({ id }: { id: string }) {
  const t = useTranslations("executions");
  const fmt = useFormatter();
  const ex = useExecution(id);
  const live = !!ex.data && LIVE.has(ex.data.status);
  const events = useExecEvents(id, live);
  // output deltas are merged into one entry so the timeline stays readable
  const rows: (ExecEvent & { merged?: number })[] = [];
  for (const e of events.data ?? []) {
    const last = rows[rows.length - 1];
    if (e.type === "output_delta" && last?.type === "output_delta") { last.merged = (last.merged ?? 1) + 1; continue; }
    rows.push({ ...e });
  }
  const start = events.data?.[0] ? new Date(events.data[0].ts).getTime() : 0;
  return (
    <Card className="flex flex-col gap-4 p-4" aria-labelledby="tl-h" data-testid="ex-timeline">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 id="tl-h" className="text-lg font-semibold">{t("timeline")}</h2>
          <p className="font-mono text-xs text-muted-foreground" dir="ltr">{id}</p>
        </div>
        {ex.data && <span className="flex items-center gap-2 text-sm font-medium" role="status"><Dot status={ex.data.status} />{t(`statuses.${ex.data.status}`)}{live && <span className="text-xs text-muted-foreground">({t("live")})</span>}</span>}
      </div>
      {ex.data && (
        <dl className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
          <div><dt className="text-muted-foreground">{t("engine")}</dt><dd>{ex.data.engine}{ex.data.is_mock ? ` (${t("mock")})` : ""}</dd></div>
          <div><dt className="text-muted-foreground">{t("model")}</dt><dd dir="ltr" className="text-start">{ex.data.provider ? `${ex.data.provider}/${ex.data.model}` : "—"}</dd></div>
          <div><dt className="text-muted-foreground">{t("tokens")}</dt><dd className="tabular-nums">{ex.data.input_tokens + ex.data.output_tokens}</dd></div>
          <div><dt className="text-muted-foreground">{t("cost")}</dt><dd className="tabular-nums">{ex.data.cost_usd ? `$${ex.data.cost_usd}` : "—"}</dd></div>
        </dl>
      )}
      {ex.data?.error && <p role="alert" className="text-sm"><span className="font-medium">{t("error")}:</span> <span dir="auto">{ex.data.error}</span></p>}
      {ex.data?.pending_question && <QuestionBox id={id} question={ex.data.pending_question} />}
      {events.isLoading && <Skeleton className="h-32" aria-busy="true" />}
      <ol className="relative flex flex-col gap-3 border-s ps-5" aria-label={t("timeline")}>
        {rows.map((e) => {
          const Icon = ICON[e.type] ?? Activity;
          return (
            <li key={e.seq} className="relative">
              <span className={cn("absolute -start-[1.85rem] top-0.5 flex size-5 items-center justify-center rounded-full border bg-background", e.type === "error" && "border-destructive")}>
                <Icon className={cn("size-3", e.type === "error" ? "text-destructive" : "text-muted-foreground")} aria-hidden />
              </span>
              <p className="text-xs text-muted-foreground">
                <span className="font-medium text-foreground">{t.has(`events.${e.type}`) ? t(`events.${e.type}`) : e.type}</span>
                {" · "}+{fmt.number(Math.max(0, (new Date(e.ts).getTime() - start) / 1000), { maximumFractionDigits: 1 })}s
                {e.merged ? ` · ${t("chunks", { n: e.merged })}` : ""}
              </p>
              {e.type !== "output_delta" && <p className="break-words text-sm" dir="auto">{summarize(e)}</p>}
            </li>
          );
        })}
      </ol>
      {ex.data?.output?.text && (
        <details className="rounded-md border p-3">
          <summary className="cursor-pointer text-sm font-medium">{t("output")}</summary>
          <pre tabIndex={0} aria-label={t("output")} className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" dir="auto">{ex.data.output.text}</pre>
        </details>
      )}
      <Button variant="ghost" size="sm" className="self-start" onClick={() => events.refetch()}>{t("refresh")}</Button>
    </Card>
  );
}

/** WAITING_FOR_INPUT: the agent asked a person; the answer resumes the execution. */
function QuestionBox({ id, question }: { id: string; question: string }) {
  const t = useTranslations("executions.question");
  const tc = useTranslations("common");
  const can = useCan();
  const [answer, setAnswer] = useState("");
  const send = useAnswerInput(id);
  const toast = useToasts((s) => s.push);
  return (
    <section className="flex flex-col gap-3 rounded-md border border-primary/40 bg-primary/5 p-3" aria-labelledby="q-h" data-testid="ex-question">
      <h3 id="q-h" className="flex items-center gap-2 text-sm font-semibold"><CircleHelp className="size-4" aria-hidden />{t("title")}</h3>
      <p className="whitespace-pre-wrap text-sm" dir="auto" data-testid="ex-question-text">{question}</p>
      {can("executions.control") ? (
        <form className="flex flex-col gap-2" onSubmit={(e) => {
          e.preventDefault();
          send.mutate(answer.trim(), {
            onSuccess: () => { setAnswer(""); toast("success", t("sent")); },
            onError: (err) => toast("error", tc("requestFailed", { code: err instanceof ApiError ? err.code : "error" })),
          });
        }}>
          <Label htmlFor="q-answer">{t("answer")}</Label>
          <Textarea id="q-answer" value={answer} onChange={(e) => setAnswer(e.target.value)} maxLength={20000} rows={3}
            aria-describedby="q-help" dir="auto" />
          <p id="q-help" className="text-xs text-muted-foreground">{t("help")}</p>
          <div><Button type="submit" disabled={!answer.trim() || send.isPending} data-testid="ex-answer">{t("send")}</Button></div>
        </form>
      ) : <p className="text-xs text-muted-foreground">{t("noPermission")}</p>}
    </section>
  );
}
