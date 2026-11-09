"use client";
import { Archive, ArchiveRestore, Bot, Copy, Eye, LayoutTemplate, Play, Plus, Power, PowerOff, Search, Square, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useState } from "react";
import { AgentStateBadge } from "@/components/agent-state-badge";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { useCan } from "@/components/providers";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { type Agent, useAgentAction, useAgents } from "@/lib/agents";
import { ApiError } from "@/lib/api";
import { useToasts } from "@/lib/toast";

const STATES = ["CREATED", "IDLE", "RUNNING", "BLOCKED", "FAILED", "DISABLED"];

export default function AgentsPage() {
  const t = useTranslations("agents");
  const tc = useTranslations("common");
  const ts = useTranslations("agentStates");
  const can = useCan();
  const toast = useToasts((s) => s.push);
  const [q, setQ] = useState("");
  const [state, setState] = useState("");
  const [archived, setArchived] = useState(false);
  const [confirm, setConfirm] = useState<{ agent: Agent; action: "archive" | "delete" } | null>(null);
  const agents = useAgents({ q, state, archived });
  const action = useAgentAction();

  function run(agent: Agent, act: string) {
    action.mutate({ id: agent.id, action: act }, {
      onSuccess: () => { toast("success", t(`done.${act}`)); setConfirm(null); },
      onError: (e) => {
        const code = e instanceof ApiError ? e.code : "network";
        toast("error", t.has(`errors.${code}`) ? t(`errors.${code}`) : tc("requestFailed", { code }));
        setConfirm(null);
      },
    });
  }

  const Act = ({ agent, act, icon: Icon, show }: { agent: Agent; act: string; icon: typeof Play; show: boolean }) =>
    show ? (
      <Button variant="ghost" size="icon" aria-label={`${t(`actions.${act}`)}: ${agent.name}`} title={t(`actions.${act}`)}
        disabled={action.isPending}
        onClick={() => (act === "archive" || act === "delete" ? setConfirm({ agent, action: act }) : run(agent, act))}>
        <Icon aria-hidden />
      </Button>
    ) : null;

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{t("title")}</h1>
          <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
        </div>
        {can("agents.write") && (
          <div className="flex gap-2">
            <Button variant="outline" asChild><Link href="/templates"><LayoutTemplate aria-hidden />{t("fromTemplate")}</Link></Button>
            <Button asChild><Link href="/agents/new" data-testid="new-agent"><Plus aria-hidden />{t("new")}</Link></Button>
          </div>
        )}
      </div>

      <Card className="flex flex-wrap items-end gap-3 p-4">
        <div className="relative min-w-56 flex-1">
          <Search className="pointer-events-none absolute start-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input className="ps-9" placeholder={t("searchPlaceholder")} aria-label={tc("search")} value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="w-48">
          <NativeSelect aria-label={t("filters.state")} value={state} onChange={(e) => setState(e.target.value)}>
            <option value="">{t("filters.state")}: {tc("all")}</option>
            {STATES.map((s) => <option key={s} value={s}>{ts(s)}</option>)}
          </NativeSelect>
        </div>
        <label className="flex min-h-10 items-center gap-2 text-sm">
          <input type="checkbox" className="size-4 accent-[var(--primary)]" checked={archived} onChange={(e) => setArchived(e.target.checked)} />
          {t("filters.archived")}
        </label>
      </Card>

      {agents.isLoading && <div className="flex flex-col gap-2" aria-busy="true">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-16" />)}</div>}
      {agents.isError && <Card role="alert" className="p-6 text-center text-destructive">{tc("error")}</Card>}
      {agents.data && agents.data.length === 0 && (
        <Card className="flex flex-col items-center gap-3 p-10 text-center">
          <Bot className="size-8 text-muted-foreground" aria-hidden />
          <p className="text-muted-foreground">{t("empty")}</p>
        </Card>
      )}
      {agents.data && agents.data.length > 0 && (
        <Card className="overflow-x-auto" tabIndex={0} aria-label={t("title")}>
          <table className="w-full min-w-[720px] text-sm" data-testid="agents-table">
            <thead className="border-b bg-muted/50 text-start text-xs text-muted-foreground">
              <tr>
                {(["name", "role", "state", "model", "priority"] as const).map((c) => <th key={c} scope="col" className="px-4 py-3 text-start font-medium">{t(`columns.${c}`)}</th>)}
                <th scope="col" className="px-4 py-3 text-end font-medium">{tc("actions")}</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {agents.data.map((a) => (
                <tr key={a.id} className="hover:bg-muted/30">
                  <td className="px-4 py-3">
                    <Link href={`/agents/${a.id}`} className="font-medium hover:underline">{a.name}</Link>
                    <div className="font-mono text-xs text-muted-foreground" dir="ltr">{a.identifier}</div>
                  </td>
                  <td className="px-4 py-3" dir="ltr">{a.role}</td>
                  <td className="px-4 py-3"><AgentStateBadge state={a.state} /></td>
                  <td className="px-4 py-3 font-mono text-xs" dir="ltr">{a.model ?? "—"}</td>
                  <td className="px-4 py-3 tabular-nums">{a.priority}</td>
                  <td className="px-4 py-3">
                    <div className="flex justify-end">
                      <Button variant="ghost" size="icon" asChild><Link href={`/agents/${a.id}`} aria-label={`${t("actions.view")}: ${a.name}`}><Eye aria-hidden /></Link></Button>
                      {!a.archived_at && <>
                        <Act agent={a} act="start" icon={Play} show={can("agents.control") && a.is_enabled && ["CREATED", "FAILED", "COMPLETED", "CANCELLED"].includes(a.state)} />
                        <Act agent={a} act="stop" icon={Square} show={can("agents.control") && ["ASSIGNED", "RUNNING", "REVIEWING", "BLOCKED", "WAITING_FOR_INPUT", "WAITING_FOR_APPROVAL"].includes(a.state)} />
                        <Act agent={a} act="enable" icon={Power} show={can("agents.control") && !a.is_enabled} />
                        <Act agent={a} act="disable" icon={PowerOff} show={can("agents.control") && a.is_enabled && a.state !== "RUNNING"} />
                        <Act agent={a} act="clone" icon={Copy} show={can("agents.write")} />
                        <Act agent={a} act="archive" icon={Archive} show={can("agents.delete")} />
                      </>}
                      {a.archived_at && <>
                        <Act agent={a} act="restore" icon={ArchiveRestore} show={can("agents.delete")} />
                        <Act agent={a} act="delete" icon={Trash2} show={can("agents.delete")} />
                      </>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {confirm && (
        <ConfirmDialog open onOpenChange={(o) => !o && setConfirm(null)} busy={action.isPending}
          title={t(confirm.action === "archive" ? "confirm.archiveTitle" : "confirm.deleteTitle")}
          description={t(confirm.action === "archive" ? "confirm.archiveBody" : "confirm.deleteBody")}
          confirmLabel={t(`actions.${confirm.action}`)} destructive={confirm.action === "delete"}
          onConfirm={() => run(confirm.agent, confirm.action)} />
      )}
    </div>
  );
}
