"use client";
import { BadgeCheck, Brain, CircleHelp, History, Lightbulb, Pencil, Plus, Search, ShieldAlert, Trash2 } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import { useEffect, useState } from "react";
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
import { ApiError } from "@/lib/api";
import { MEMORY_TYPES, type Memory, PROJECT_TYPES, type Verification, useContextPreview, useMemories, useMemory, useMemoryActions } from "@/lib/memory";
import { useProjects, useTasks } from "@/lib/tasks";
import { useToasts } from "@/lib/toast";
import { cn } from "@/lib/utils";

const VER: Record<Verification, { icon: typeof BadgeCheck; tone: string }> = {
  verified: { icon: BadgeCheck, tone: "text-success" },
  assumption: { icon: CircleHelp, tone: "text-warning" },
  suggestion: { icon: Lightbulb, tone: "text-muted-foreground" },
};

function VerificationLabel({ v }: { v: Verification }) {
  const t = useTranslations("memory");
  const { icon: Icon, tone } = VER[v] ?? VER.assumption;
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium" data-testid="mem-verification">
      <Icon className={cn("size-3.5", tone)} aria-hidden />{t(`verification.${v}`)}
    </span>
  );
}

function provenance(m: Memory): string {
  const s = m.source;
  return [s.kind, s.ref, s.execution_id ? `exec ${String(s.execution_id).slice(0, 8)}` : null].filter(Boolean).join(" · ");
}

export default function MemoryPage() {
  const t = useTranslations("memory");
  const tc = useTranslations("common");
  const can = useCan();
  const projects = useProjects();
  const [projectId, setProjectId] = useState<number>();
  useEffect(() => { if (!projectId && projects.data?.length) setProjectId(projects.data[0].id); }, [projects.data, projectId]);
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [verification, setVerification] = useState("");
  const list = useMemories(projectId, { q, type, verification });
  const [editing, setEditing] = useState<Memory | null>(null);
  const [deleting, setDeleting] = useState<Memory | null>(null);
  const [adding, setAdding] = useState(false);
  const actions = useMemoryActions();
  const toast = useToasts((s) => s.push);
  const fail = (e: unknown) => toast("error", tc("requestFailed", { code: e instanceof ApiError ? e.code : "error" }));
  const writable = can("memory.write");

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{t("title")}</h1>
          <p className="max-w-3xl text-sm text-muted-foreground">{t("subtitle")}</p>
        </div>
        {writable && projectId && <Button onClick={() => setAdding(true)} data-testid="mem-add"><Plus aria-hidden />{t("add")}</Button>}
      </div>

      <Card className="p-4">
        <form className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5" role="search" aria-label={t("filters")}
          onSubmit={(e) => { e.preventDefault(); setQ(qInput.trim()); }}>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="m-project">{t("project")}</Label>
            <NativeSelect id="m-project" value={projectId ?? ""} onChange={(e) => setProjectId(Number(e.target.value) || undefined)}>
              {projects.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </NativeSelect>
          </div>
          <div className="flex flex-col gap-1.5 lg:col-span-2">
            <Label htmlFor="m-q">{t("search")}</Label>
            <div className="flex gap-2">
              <Input id="m-q" value={qInput} onChange={(e) => setQInput(e.target.value)} dir="auto" maxLength={500} />
              <Button type="submit" variant="secondary" size="icon" aria-label={t("search")}><Search aria-hidden /></Button>
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="m-type">{t("type")}</Label>
            <NativeSelect id="m-type" value={type} disabled={!!q} onChange={(e) => setType(e.target.value)}>
              <option value="">{tc("all")}</option>
              {MEMORY_TYPES.map((k) => <option key={k} value={k}>{t(`types.${k}`)}</option>)}
            </NativeSelect>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="m-ver">{t("verificationLabel")}</Label>
            <NativeSelect id="m-ver" value={verification} disabled={!!q} onChange={(e) => setVerification(e.target.value)}>
              <option value="">{tc("all")}</option>
              {(["verified", "assumption", "suggestion"] as const).map((k) => <option key={k} value={k}>{t(`verification.${k}`)}</option>)}
            </NativeSelect>
          </div>
        </form>
        {q && (
          <p className="mt-3 flex flex-wrap items-center gap-2 text-sm text-muted-foreground" role="status">
            {t("rankedFor", { q })}
            <Button variant="ghost" size="sm" onClick={() => { setQ(""); setQInput(""); }}>{t("clearSearch")}</Button>
          </p>
        )}
      </Card>

      {(projects.isLoading || list.isLoading) && <Skeleton className="h-48" aria-busy="true" />}
      {list.isError && <Card role="alert" className="p-6 text-center text-destructive">{tc("error")}</Card>}
      {projects.data?.length === 0 && <Card className="p-8 text-center text-muted-foreground">{t("noProjects")}</Card>}
      {list.data && list.data.length === 0 && (
        <Card className="flex flex-col items-center gap-3 p-10 text-center">
          <Brain className="size-8 text-muted-foreground" aria-hidden /><p className="text-muted-foreground">{t("empty")}</p>
        </Card>
      )}
      {list.data && list.data.length > 0 && (
        <ul className="flex flex-col gap-3" data-testid="mem-list" aria-label={t("listLabel")}>
          {list.data.map((m) => (
            <li key={m.id}>
              <Card className="flex flex-col gap-2 p-4" data-testid={`mem-${m.id}`}>
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h2 className="text-base font-semibold" dir="auto">{m.title}</h2>
                    <p className="text-xs text-muted-foreground">
                      {t(`types.${m.memory_type}`)} · {t("version", { n: m.version })}
                      {m.score !== undefined && <> · {t("score", { s: m.score.toFixed(2) })}</>}
                    </p>
                  </div>
                  <VerificationLabel v={m.verification} />
                </div>
                <p className="whitespace-pre-wrap text-sm" dir="auto">{m.content}</p>
                <p className="text-xs text-muted-foreground"><span>{t("provenance")}: </span><span dir="ltr" className="font-mono">{provenance(m)}</span></p>
                {writable && (
                  <div className="flex flex-wrap gap-2 pt-1">
                    {m.verification !== "verified" && (
                      <Button size="sm" variant="outline" disabled={actions.verify.isPending}
                        onClick={() => actions.verify.mutate(m.id, { onSuccess: () => toast("success", t("verified")), onError: fail })}>
                        <BadgeCheck aria-hidden />{t("verify")}
                      </Button>
                    )}
                    <Button size="sm" variant="outline" onClick={() => setEditing(m)} aria-label={`${t("correct")}: ${m.title}`}>
                      <Pencil aria-hidden />{t("correct")}
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => setDeleting(m)} aria-label={`${t("delete")}: ${m.title}`}>
                      <Trash2 aria-hidden />{t("delete")}
                    </Button>
                  </div>
                )}
              </Card>
            </li>
          ))}
        </ul>
      )}

      {projectId && <ContextPreviewCard projectId={projectId} />}

      {adding && projectId && (
        <MemoryDialog title={t("add")} onClose={() => setAdding(false)} busy={actions.create.isPending}
          onSubmit={(v) => actions.create.mutate({ ...v, project_id: projectId }, {
            onSuccess: () => { setAdding(false); toast("success", tc("saved")); }, onError: fail,
          })} />
      )}
      {editing && (
        <MemoryDialog title={t("correctTitle")} memory={editing} onClose={() => setEditing(null)} busy={actions.correct.isPending}
          onSubmit={(v) => actions.correct.mutate({ id: editing.id, title: v.title, content: v.content }, {
            onSuccess: () => { setEditing(null); toast("success", t("corrected")); }, onError: fail,
          })} />
      )}
      <ConfirmDialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(null)} destructive
        title={t("deleteTitle")} description={t("deleteBody", { title: deleting?.title ?? "" })} confirmLabel={t("delete")}
        busy={actions.remove.isPending}
        onConfirm={() => deleting && actions.remove.mutate(deleting.id, {
          onSuccess: () => { setDeleting(null); toast("success", t("deleted")); }, onError: fail,
        })} />
    </div>
  );
}

function MemoryDialog({ title, memory, onClose, onSubmit, busy }: {
  title: string; memory?: Memory; onClose: () => void; busy: boolean;
  onSubmit: (v: { memory_type: string; title: string; content: string }) => void;
}) {
  const t = useTranslations("memory");
  const tc = useTranslations("common");
  const fmt = useFormatter();
  const detail = useMemory(memory?.id);
  const [type, setType] = useState(memory?.memory_type ?? "technical_decision");
  const [name, setName] = useState(memory?.title ?? "");
  const [content, setContent] = useState(memory?.content ?? "");
  const history = detail.data?.history ?? [];
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription>{memory ? t("correctHelp") : t("addHelp")}</DialogDescription>
        <form className="mt-4 flex flex-col gap-4" onSubmit={(e) => { e.preventDefault(); onSubmit({ memory_type: type, title: name.trim(), content: content.trim() }); }}>
          {!memory && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="md-type">{t("type")}</Label>
              <NativeSelect id="md-type" value={type} onChange={(e) => setType(e.target.value)}>
                {PROJECT_TYPES.map((k) => <option key={k} value={k}>{t(`types.${k}`)}</option>)}
              </NativeSelect>
            </div>
          )}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="md-title">{t("fieldTitle")}</Label>
            <Input id="md-title" required maxLength={255} value={name} onChange={(e) => setName(e.target.value)} dir="auto" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="md-content">{t("fieldContent")}</Label>
            <Textarea id="md-content" required maxLength={20000} rows={6} value={content} onChange={(e) => setContent(e.target.value)} dir="auto" />
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>{tc("cancel")}</Button>
            <Button type="submit" disabled={busy || !name.trim() || !content.trim()} data-testid="md-save">{busy ? tc("saving") : tc("save")}</Button>
          </div>
        </form>
        {history.length > 0 && (
          <section className="mt-6" aria-labelledby="md-history">
            <h3 id="md-history" className="flex items-center gap-1.5 text-sm font-semibold"><History className="size-4" aria-hidden />{t("history")}</h3>
            <ol className="mt-2 flex flex-col gap-2 text-sm">
              {history.map((h) => (
                <li key={h.id} className="rounded-md border p-2">
                  <p className="text-xs text-muted-foreground">{t("version", { n: h.version })} · {fmt.dateTime(new Date(h.created_at), { dateStyle: "medium", timeStyle: "short" })}</p>
                  <p className="whitespace-pre-wrap" dir="auto">{h.content}</p>
                </li>
              ))}
            </ol>
          </section>
        )}
      </DialogContent>
    </Dialog>
  );
}

const KIND_ORDER = ["requirements", "user", "tool_output", "retrieved"] as const;

function ContextPreviewCard({ projectId }: { projectId: number }) {
  const t = useTranslations("memory");
  const tc = useTranslations("common");
  const tasks = useTasks({ projectId });
  const [taskId, setTaskId] = useState<number>();
  const preview = useContextPreview(taskId);
  const m = preview.data?.manifest;
  return (
    <Card className="flex flex-col gap-4 p-4" aria-labelledby="ctx-title">
      <div>
        <h2 id="ctx-title" className="text-lg font-semibold">{t("contextTitle")}</h2>
        <p className="text-sm text-muted-foreground">{t("contextHelp")}</p>
      </div>
      <div className="flex max-w-md flex-col gap-1.5">
        <Label htmlFor="ctx-task">{t("task")}</Label>
        <NativeSelect id="ctx-task" value={taskId ?? ""} onChange={(e) => setTaskId(Number(e.target.value) || undefined)}>
          <option value="">{tc("none")}</option>
          {tasks.data?.map((x) => <option key={x.id} value={x.id}>#{x.id} {x.title}</option>)}
        </NativeSelect>
      </div>
      {preview.isLoading && <Skeleton className="h-32" aria-busy="true" />}
      {m && (
        <div className="flex flex-col gap-4" data-testid="ctx-preview">
          <p className="text-sm" role="status">{t("budgetUse", { used: m.estimated_tokens, budget: m.budget, type: m.task_type })}</p>
          <div className="overflow-x-auto" tabIndex={0} aria-label={t("segments")}>
            <table className="w-full min-w-[560px] text-sm">
              <thead className="border-b text-xs text-muted-foreground">
                <tr>
                  <th scope="col" className="px-3 py-2 text-start font-medium">{t("segment")}</th>
                  <th scope="col" className="px-3 py-2 text-start font-medium">{t("item")}</th>
                  <th scope="col" className="px-3 py-2 text-start font-medium">{t("trust")}</th>
                  <th scope="col" className="px-3 py-2 text-end font-medium">{t("tokens")}</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {KIND_ORDER.flatMap((k) => (m.segments[k] ?? []).map((s, i) => (
                  <tr key={`${k}-${i}`}>
                    <td className="px-3 py-2">{t(`kinds.${k}`)}</td>
                    <td className="px-3 py-2" dir="auto">
                      {s.title === "task" ? t("itemTask") : s.title === "review note" ? t("itemReviewNote") : s.title}
                      {m.flagged.includes(s.title) && (
                        <span className="ms-2 inline-flex items-center gap-1 text-xs font-medium"><ShieldAlert className="size-3.5 text-warning" aria-hidden />{t("flagged")}</span>
                      )}
                    </td>
                    <td className="px-3 py-2">{s.trusted ? t("trusted") : t("untrusted")}</td>
                    <td className="px-3 py-2 text-end tabular-nums">{s.tokens}</td>
                  </tr>
                )))}
              </tbody>
            </table>
          </div>
          {(m.dropped.length > 0 || m.compressed.length > 0) && (
            <ul className="flex flex-col gap-1 text-sm text-muted-foreground">
              {m.compressed.map((c, i) => <li key={`c${i}`}><span dir="auto">{t("compressedItem", { title: c.title, from: c.from, to: c.to })}</span></li>)}
              {m.dropped.map((d, i) => <li key={`d${i}`}><span dir="auto">{t("droppedItem", { title: d.title, reason: t(`reasons.${d.reason}`) })}</span></li>)}
            </ul>
          )}
          <details className="rounded-md border p-3">
            <summary className="cursor-pointer text-sm font-medium">{t("showPrompt")}</summary>
            <h3 className="mt-3 text-xs font-semibold text-muted-foreground">{t("systemMessage")}</h3>
            <pre tabIndex={0} aria-label={t("systemMessage")} className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-3 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" dir="auto">{preview.data!.system}</pre>
            <h3 className="mt-3 text-xs font-semibold text-muted-foreground">{t("userMessage")}</h3>
            <pre tabIndex={0} aria-label={t("userMessage")} className="mt-1 max-h-96 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-3 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" dir="auto">{preview.data!.prompt}</pre>
          </details>
        </div>
      )}
    </Card>
  );
}
