"use client";
import { AlertTriangle, CheckCircle2, ClipboardCopy, Download, Info, Paperclip, RotateCcw, Send, ShieldCheck, Trash2, UserCheck, XCircle } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import Link from "next/link";
import { useId, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { LifecycleBadge, MockBadge, ModeBadge, useActor, useFail, useReason } from "@/components/workforce/shared";
import { useToasts } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { type Submission, type TaskDetail, useWfTask, useWorkforce, useWorkforceActions } from "@/lib/workforce";

function Section({ title, children, testid }: { title: string; children: React.ReactNode; testid?: string }) {
  return (
    <section className="flex flex-col gap-2" data-testid={testid}>
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

function Notice({ tone = "info", children, testid }: { tone?: "info" | "warning"; children: React.ReactNode; testid?: string }) {
  const Icon = tone === "warning" ? AlertTriangle : Info;
  return (
    <div role="note" className={cn("flex gap-3 rounded-md border p-3 text-sm", tone === "warning" && "border-warning")} data-testid={testid}>
      <Icon className={cn("mt-0.5 size-5 shrink-0", tone === "warning" ? "text-warning" : "text-primary")} aria-hidden />
      <div className="flex min-w-0 flex-col gap-1">{children}</div>
    </div>
  );
}

/** One line of text plus a button: decline, reopen, cancel. The reason is required and kept in the history. */
function NoteForm({ label, button, onSubmit, busy, destructive, testid }: {
  label: string; button: string; onSubmit: (note: string) => void; busy: boolean; destructive?: boolean; testid: string;
}) {
  const id = useId();
  const [note, setNote] = useState("");
  return (
    <form className="flex flex-col gap-1.5" onSubmit={(e) => { e.preventDefault(); if (note.trim()) onSubmit(note.trim()); }}>
      <Label htmlFor={id}>{label}</Label>
      <div className="flex flex-wrap gap-2">
        <Input id={id} value={note} onChange={(e) => setNote(e.target.value)} required maxLength={500} dir="auto"
          className="min-w-48 flex-1" data-testid={`${testid}-note`} />
        <Button type="submit" variant={destructive ? "destructive" : "outline"} disabled={busy || !note.trim()} data-testid={testid}>{button}</Button>
      </div>
    </form>
  );
}

export function TaskDialog({ id, onClose }: { id: number; onClose: () => void }) {
  const t = useTranslations("workforce.task");
  const tc = useTranslations("common");
  const task = useWfTask(id);
  const d = task.data;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="flex max-h-[90dvh] max-w-3xl flex-col gap-5 overflow-y-auto" data-testid="wf-task-dialog">
        {!d && (
          <>
            <DialogTitle>{t("title", { id })}</DialogTitle>
            <DialogDescription>{task.isError ? tc("error") : tc("loading")}</DialogDescription>
            {task.isLoading && <Skeleton className="h-40" />}
          </>
        )}
        {d && <TaskBody d={d} />}
      </DialogContent>
    </Dialog>
  );
}

function TaskBody({ d }: { d: TaskDetail }) {
  const t = useTranslations("workforce.task");
  const tw = useTranslations("workforce");
  const reason = useReason();
  const fmt = useFormatter();
  const actor = useActor();
  const latest = d.submissions[d.submissions.length - 1];
  return (
    <>
      <div className="flex flex-col gap-2 pe-8">
        <DialogTitle dir="auto"><span className="font-mono text-primary" dir="ltr">#{d.id}</span> {d.title}</DialogTitle>
        <DialogDescription className="mt-0">
          <Link href={`/workforce/objectives/${d.objective.id}`} className="font-medium text-primary underline underline-offset-4" dir="auto">
            {t("ofObjective", { title: d.objective.title })}
          </Link>
        </DialogDescription>
        <div className="flex flex-wrap items-center gap-2">
          <LifecycleBadge state={d.lifecycle} />
          {d.mode && <ModeBadge mode={d.mode} />}
          {d.worker && <span className="text-xs text-muted-foreground"><span className="font-mono" dir="ltr">{d.worker.label}</span> <span dir="auto">{d.worker.name}</span></span>}
          <span className="text-xs text-muted-foreground">{t("attempts", { n: d.attempts, max: d.max_attempts })}</span>
          {d.critical_operation && (
            <span className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium">
              <ShieldCheck className="size-3.5 text-warning" aria-hidden />{t("critical")} <span className="font-mono" dir="ltr">{d.critical_operation}</span>
            </span>
          )}
        </div>
      </div>

      {d.needs_human_review && (
        <Notice tone="warning" testid="wf-needs-human">
          <p className="font-medium">{t("needsHuman")}</p>
          <p>{tw.has(`reviewReason.${(d.review_reason ?? "").split(":")[0]}`) ? tw(`reviewReason.${(d.review_reason ?? "").split(":")[0]}`) : d.review_reason}</p>
        </Notice>
      )}
      {d.waiting && <Notice testid="wf-waiting"><p>{t("waiting", { reason: reason(d.waiting.reason) })}</p></Notice>}
      {d.fallback && (
        <Notice tone="warning" testid="wf-fallback">
          <p>{t("fallback", { reason: reason(d.fallback.reason) })}</p>
          {d.fallback.note && <p className="font-medium">{t("fallbackMayHaveArrived")}</p>}
        </Notice>
      )}
      {d.approval_id && (
        <Notice testid="wf-approval">
          <p>{t("needsApproval")}</p>
          {d.submissions[d.submissions.length - 1]?.submitted_by.startsWith("user:") && <p>{t("needsApprovalOther")}</p>}
          <Link href={`/approvals?id=${d.approval_id}`} className="w-fit font-medium text-primary underline underline-offset-4">{t("openApproval", { id: d.approval_id })}</Link>
        </Notice>
      )}
      {d.error && <p role="alert" className="text-sm text-destructive" dir="auto">{d.error}</p>}

      <Section title={t("what")}>
        <dl className="grid gap-x-4 gap-y-2 text-sm sm:grid-cols-[10rem_1fr]">
          {d.task_objective && (<><dt className="text-muted-foreground">{t("objective")}</dt><dd dir="auto">{d.task_objective}</dd></>)}
          {d.instructions && (<><dt className="text-muted-foreground">{t("instructions")}</dt><dd className="whitespace-pre-wrap" dir="auto">{d.instructions}</dd></>)}
          {d.expected_output && (<><dt className="text-muted-foreground">{t("expected")}</dt><dd dir="auto">{d.expected_output}</dd></>)}
        </dl>
        {d.acceptance_criteria.length > 0 && (
          <div className="text-sm">
            <p className="text-muted-foreground">{t("criteria")}</p>
            <ol className="ms-5 list-decimal">{d.acceptance_criteria.map((c, i) => <li key={i}><bdi>{c}</bdi></li>)}</ol>
          </div>
        )}
        {d.depends_on.length > 0 && (
          <div className="text-sm">
            <p className="text-muted-foreground">{t("dependsOn")}</p>
            <ul className="flex flex-col gap-1">
              {d.depends_on.map((p) => (
                <li key={p.id} className="flex flex-wrap items-center gap-2"><span className="font-mono text-xs" dir="ltr">#{p.id}</span><span dir="auto">{p.title}</span><LifecycleBadge state={p.lifecycle} /></li>
              ))}
            </ul>
          </div>
        )}
        {d.review_notes.length > 0 && (
          <div className="text-sm" data-testid="wf-review-notes">
            <p className="text-muted-foreground">{t("reviewNotes")}</p>
            <ul className="ms-5 list-disc">{d.review_notes.map((n, i) => <li key={i} className="whitespace-pre-wrap"><bdi>{n}</bdi></li>)}</ul>
          </div>
        )}
      </Section>

      {d.brief && <Brief d={d} />}
      <Work d={d} />
      {d.can.review && latest && latest.decision === null && <ReviewBox d={d} sub={latest} />}
      <Manage d={d} />

      {d.submissions.length > 0 && (
        <Section title={t("versions", { n: d.submissions.length })} testid="wf-submissions">
          <ul className="flex flex-col gap-3">
            {[...d.submissions].reverse().map((s) => <li key={s.id}><SubmissionCard s={s} criteria={d.acceptance_criteria} /></li>)}
          </ul>
        </Section>
      )}

      {d.failures.length > 0 && (
        <Section title={t("failures")} testid="wf-failures">
          <ul className="flex flex-col gap-2 text-sm">
            {d.failures.map((f, i) => (
              <li key={i} className="rounded-md border p-3">
                <p className="font-medium">
                  {t("failureLine", { attempt: f.attempt })} — <span className="font-mono" dir="ltr">{f.code}</span> · {tw.has(`failureAction.${f.action}`) ? tw(`failureAction.${f.action}`) : f.action}
                </p>
                <p className="text-xs text-muted-foreground">{f.delivered === false ? t("notDelivered") : f.delivered ? t("delivered") : t("deliveryUnknown")}</p>
                {f.message && <p className="mt-1 break-words text-xs text-muted-foreground" lang="en" dir="ltr">{f.message}</p>}
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title={t("history")} testid="wf-transitions">
        <ol className="flex flex-col gap-1.5 text-sm">
          {d.transitions.map((x) => (
            <li key={x.id} className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <LifecycleBadge state={x.to} />
              <span className="text-xs text-muted-foreground">{actor(x.actor)}</span>
              <span className="text-xs text-muted-foreground">{fmt.dateTime(new Date(x.at), { dateStyle: "short", timeStyle: "medium" })}</span>
              {x.reason && <span className="basis-full text-xs text-muted-foreground" dir="auto">{x.reason}</span>}
            </li>
          ))}
        </ol>
      </Section>
    </>
  );
}

function Brief({ d }: { d: TaskDetail }) {
  const t = useTranslations("workforce.task");
  const toast = useToasts((s) => s.push);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(d.brief!.message);
      toast("success", t("copied"));
    } catch {
      toast("error", t("copyFailed"));
    }
  };
  return (
    <Section title={t("brief")} testid="wf-brief">
      <p className="text-sm text-muted-foreground">{t("briefHelp")}</p>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-muted p-3 font-mono text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" dir="ltr" tabIndex={0}
        aria-label={t("brief")} data-testid="wf-brief-text">{d.brief!.message}</pre>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" variant="outline" size="sm" onClick={copy} data-testid="wf-brief-copy"><ClipboardCopy aria-hidden />{t("copy")}</Button>
        <span className="text-xs text-muted-foreground">{t("briefChars", { n: d.brief!.chars })}</span>
      </div>
    </Section>
  );
}

/** What the person doing the work can do: take the card, hand in the result, or give it back. */
function Work({ d }: { d: TaskDetail }) {
  const t = useTranslations("workforce.task");
  const a = useWorkforceActions();
  const fail = useFail();
  const toast = useToasts((s) => s.push);
  const fmt = useFormatter();
  const file = useRef<HTMLInputElement>(null);
  const [content, setContent] = useState("");
  const [notes, setNotes] = useState("");
  if (!d.can.accept && !d.can.submit && !d.can.decline) return null;
  const upload = (files: FileList | null) => {
    for (const f of Array.from(files ?? [])) a.upload.mutate({ id: d.id, file: f }, { onError: fail });
    if (file.current) file.current.value = "";
  };
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!content.trim()) return;
    a.submit.mutate({ id: d.id, content, notes, attachment_ids: d.pending_attachments.map((x) => x.id) }, {
      onError: fail, onSuccess: () => { toast("success", t("submitted")); setContent(""); setNotes(""); },
    });
  };
  return (
    <Section title={t("yourWork")} testid="wf-work">
      {d.can.accept && (
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={() => a.accept.mutate(d.id, { onError: fail })} disabled={a.accept.isPending} data-testid="wf-accept">
            <UserCheck aria-hidden />{t("accept")}
          </Button>
          <span className="text-sm text-muted-foreground">{t("acceptHelp")}</span>
        </div>
      )}
      {d.can.submit && (
        <form onSubmit={submit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wf-result">{t("result")}</Label>
            <Textarea id="wf-result" value={content} onChange={(e) => setContent(e.target.value)} required dir="auto"
              maxLength={d.limits.content_chars} className="min-h-40" aria-describedby="wf-result-help" data-testid="wf-result" />
            <p id="wf-result-help" className="text-xs text-muted-foreground">{t("resultHelp")}</p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wf-notes">{t("notes")}</Label>
            <Textarea id="wf-notes" value={notes} onChange={(e) => setNotes(e.target.value)} dir="auto" maxLength={d.limits.notes_chars}
              className="min-h-16" data-testid="wf-notes" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="wf-files">{t("attachments")}</Label>
            <input id="wf-files" ref={file} type="file" multiple onChange={(e) => upload(e.target.files)} aria-describedby="wf-files-help"
              className="text-sm file:me-3 file:h-9 file:rounded-md file:border file:border-border file:bg-card file:px-3 file:text-sm file:font-medium" data-testid="wf-files" />
            <p id="wf-files-help" className="text-xs text-muted-foreground">
              {t("attachmentsHelp", { n: d.limits.attachments, mb: fmt.number(d.limits.attachment_bytes / 1048576, { maximumFractionDigits: 0 }) })}
            </p>
            {d.pending_attachments.length > 0 && (
              <ul className="flex flex-col gap-1" data-testid="wf-pending-files">
                {d.pending_attachments.map((x) => (
                  <li key={x.id} className="flex items-center gap-2 text-sm">
                    <Paperclip className="size-4 text-muted-foreground" aria-hidden />
                    <span className="min-w-0 truncate" dir="ltr">{x.filename}</span>
                    <span className="text-xs text-muted-foreground">{t("kb", { n: fmt.number(x.size / 1024, { maximumFractionDigits: 1 }) })}</span>
                    <Button type="button" variant="ghost" size="icon" className="ms-auto" aria-label={t("removeFile", { name: x.filename })}
                      onClick={() => a.removeAttachment.mutate(x.id, { onError: fail })}><Trash2 aria-hidden /></Button>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="flex justify-end">
            <Button type="submit" disabled={a.submit.isPending || !content.trim()} data-testid="wf-submit-result"><Send aria-hidden />{t("submit")}</Button>
          </div>
        </form>
      )}
      {d.can.decline && (
        <details className="rounded-md border p-3">
          <summary className="cursor-pointer text-sm font-medium">{t("declineTitle")}</summary>
          <div className="mt-3">
            <NoteForm label={t("declineReason")} button={t("decline")} busy={a.decline.isPending} testid="wf-decline"
              onSubmit={(reason) => a.decline.mutate({ id: d.id, reason }, { onError: fail })} />
          </div>
        </details>
      )}
    </Section>
  );
}

/** A person's decision on the latest result. The Manager's verdict is shown as advice. */
function ReviewBox({ d, sub }: { d: TaskDetail; sub: Submission }) {
  const t = useTranslations("workforce.task");
  const a = useWorkforceActions();
  const fail = useFail();
  const toast = useToasts((s) => s.push);
  const [note, setNote] = useState("");
  const [missing, setMissing] = useState(false);
  const decide = (decision: "approve" | "revise" | "reject") => {
    if (decision !== "approve" && !note.trim()) { setMissing(true); return; }
    setMissing(false);
    a.review.mutate({ id: d.id, decision, note: note.trim() }, {
      onError: fail, onSuccess: () => { toast("success", t(`decided.${decision}`)); setNote(""); },
    });
  };
  return (
    <Section title={t("reviewTitle", { version: sub.version })} testid="wf-review">
      <p className="text-sm text-muted-foreground">{d.critical_operation ? t("reviewHelpCritical") : t("reviewHelp")}</p>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="wf-review-note">{t("reviewNote")}</Label>
        <Textarea id="wf-review-note" value={note} onChange={(e) => { setNote(e.target.value); setMissing(false); }} dir="auto" maxLength={4000}
          className="min-h-16" aria-invalid={missing || undefined} aria-describedby={missing ? "wf-review-note-err" : undefined} data-testid="wf-review-note" />
        {missing && <p id="wf-review-note-err" role="alert" className="text-sm text-destructive">{t("reviewNoteRequired")}</p>}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => decide("approve")} disabled={a.review.isPending} data-testid="wf-review-approve"><CheckCircle2 aria-hidden />{t("approve")}</Button>
        <Button variant="outline" onClick={() => decide("revise")} disabled={a.review.isPending} data-testid="wf-review-revise"><RotateCcw aria-hidden />{t("revise")}</Button>
        <Button variant="destructive" onClick={() => decide("reject")} disabled={a.review.isPending} data-testid="wf-review-reject"><XCircle aria-hidden />{t("reject")}</Button>
        {d.needs_human_review && (
          <Button variant="ghost" className="ms-auto" onClick={() => a.reviewRetry.mutate(d.id, { onError: fail, onSuccess: () => toast("success", t("askedAgain")) })}
            disabled={a.reviewRetry.isPending} data-testid="wf-review-retry">{t("askManagerAgain")}</Button>
        )}
      </div>
    </Section>
  );
}

/** Reassign, reopen, cancel: each needs a reason, and each is recorded in the task's history and the audit log. */
function Manage({ d }: { d: TaskDetail }) {
  const t = useTranslations("workforce.task");
  const a = useWorkforceActions();
  const fail = useFail();
  const force = useWorkforce().data?.workforce;
  const [worker, setWorker] = useState("");
  if (!d.can.reassign && !d.can.reopen && !d.can.cancel) return null;
  const others = (force?.workers ?? []).filter((w) => w.id !== d.worker?.id && w.mode !== "DISABLED");
  return (
    <details className="rounded-md border p-3" data-testid="wf-manage">
      <summary className="cursor-pointer text-sm font-medium">{t("manage")}</summary>
      <div className="mt-3 flex flex-col gap-4">
        {d.can.reassign && others.length > 0 && (
          <div className="flex flex-col gap-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="wf-reassign-worker">{t("reassignTo")}</Label>
              <NativeSelect id="wf-reassign-worker" value={worker} onChange={(e) => setWorker(e.target.value)} data-testid="wf-reassign-worker">
                <option value="">{t("chooseWorker")}</option>
                {others.map((w) => <option key={w.id} value={w.id}>{w.label} — {w.name}</option>)}
              </NativeSelect>
            </div>
            <NoteForm label={t("reassignNote")} button={t("reassign")} busy={a.reassign.isPending || !worker} testid="wf-reassign"
              onSubmit={(note) => worker && a.reassign.mutate({ id: d.id, worker_id: Number(worker), note }, { onError: fail, onSuccess: () => setWorker("") })} />
          </div>
        )}
        {d.can.reopen && (
          <NoteForm label={t("reopenNote")} button={t("reopen")} busy={a.reopen.isPending} testid="wf-reopen"
            onSubmit={(note) => a.reopen.mutate({ id: d.id, note }, { onError: fail })} />
        )}
        {d.can.cancel && (
          <NoteForm label={t("cancelNote")} button={t("cancel")} busy={a.cancelTask.isPending} destructive testid="wf-cancel"
            onSubmit={(note) => a.cancelTask.mutate({ id: d.id, note }, { onError: fail })} />
        )}
      </div>
    </details>
  );
}

function SubmissionCard({ s, criteria }: { s: Submission; criteria: string[] }) {
  const t = useTranslations("workforce.task");
  const tw = useTranslations("workforce");
  const actor = useActor();
  const fmt = useFormatter();
  const r = s.review;
  const head = (r?.reason ?? "").split(":")[0];
  return (
    <article className="flex flex-col gap-2 rounded-md border p-3 text-sm" data-testid={`wf-submission-${s.version}`}>
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-semibold">{t("version", { n: s.version })}</span>
        <span className="text-xs text-muted-foreground">{s.mode === "HUMAN_ASSISTED" ? t("byPerson", { who: actor(s.submitted_by) }) : t("byProvider", { source: s.source })}</span>
        <span className="text-xs text-muted-foreground">{fmt.dateTime(new Date(s.created_at), { dateStyle: "short", timeStyle: "short" })}</span>
        {s.is_mock && <MockBadge short />}
        <span className="ms-auto text-xs font-medium" data-testid="wf-submission-decision">
          {s.decision ? t(`decision.${s.decision}`, { who: actor(s.decided_by) }) : t("decision.none")}
        </span>
      </header>
      <details open={s.decision === null}>
        <summary className="cursor-pointer text-xs font-medium text-primary">{t("showResult", { n: s.content.length })}</summary>
        <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted p-3 font-sans text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" dir="auto" tabIndex={0}
          aria-label={t("version", { n: s.version })} data-testid="wf-submission-content">{s.content}</pre>
      </details>
      {s.notes && <p className="text-xs" dir="auto"><span className="text-muted-foreground">{t("notes")}: </span>{s.notes}</p>}
      {s.attachments.length > 0 && (
        <ul className="flex flex-wrap gap-2">
          {s.attachments.map((x) => (
            <li key={x.id}>
              <a href={`/api/workforce/attachments/${x.id}`} download={x.filename}
                className="inline-flex min-h-9 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring">
                <Download className="size-3.5" aria-hidden /><span dir="ltr">{x.filename}</span>
              </a>
            </li>
          ))}
        </ul>
      )}
      {s.provider?.request_id && (
        <p className="text-xs text-muted-foreground">
          {t("providerRequest")}: <span className="font-mono" dir="ltr">{s.provider.request_id}</span>
          {s.provider.usage_reported === false ? ` · ${t("usageNotReported")}` : ""}
        </p>
      )}
      {r && (
        <div className="flex flex-col gap-1 rounded-md bg-muted p-3 text-xs" data-testid="wf-submission-review">
          <p className="font-medium">
            {r.manager ? t("managerAdvice", { decision: tw.has(`advice.${r.advisory ?? ""}`) ? tw(`advice.${r.advisory}`) : (r.advisory ?? "—") }) : t("noManagerVerdict")}
            {r.manager?.is_mock ? ` · ${tw("mockShort")}` : ""}
          </p>
          {r.needs_human && <p>{tw.has(`reviewReason.${head}`) ? tw(`reviewReason.${head}`) : r.reason}</p>}
          {r.overridden && <p>{tw.has(`overridden.${r.overridden}`) ? tw(`overridden.${r.overridden}`) : r.overridden}</p>}
          {r.verdict && r.verdict.criteria.length > 0 && (
            <ul className="flex flex-col gap-1">
              {r.verdict.criteria.map((c) => {
                const quoted = r.evidence_verified?.[String(c.index)];
                return (
                  <li key={c.index} className="flex gap-2">
                    {c.met ? <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-success" aria-hidden /> : <XCircle className="mt-0.5 size-3.5 shrink-0 text-destructive" aria-hidden />}
                    <span>
                      <span dir="auto">{criteria[c.index - 1] ?? `#${c.index}`}</span>{" — "}
                      {c.met ? (quoted ? t("criterionQuoted") : t("criterionClaimed")) : t("criterionUnmet")}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
          {r.verdict?.feedback && <p className="whitespace-pre-wrap" dir="auto">{r.verdict.feedback}</p>}
          {r.human?.note && <p dir="auto"><span className="text-muted-foreground">{t("humanNote", { who: actor(r.human.by) })}: </span>{r.human.note}</p>}
          {r.human?.self_reviewed && <p>{t("selfReviewed")}</p>}
        </div>
      )}
    </article>
  );
}
