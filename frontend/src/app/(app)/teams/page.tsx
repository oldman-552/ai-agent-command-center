"use client";
import { Crown, Plus, Trash2, Users } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
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
import { type Team, useTeamActions, useTeams, useUsers } from "@/lib/admin";
import { useAgents } from "@/lib/agents";
import { ApiError } from "@/lib/api";
import { useToasts } from "@/lib/toast";

export default function TeamsPage() {
  const t = useTranslations("teams");
  const tc = useTranslations("common");
  const can = useCan();
  const teams = useTeams();
  const agents = useAgents({});
  const a = useTeamActions();
  const toast = useToasts((s) => s.push);
  const [editing, setEditing] = useState<Team | "new">();
  const [deleting, setDeleting] = useState<Team>();
  const agentName = (id: number) => agents.data?.find((x) => x.id === id)?.name ?? `#${id}`;
  const write = can("teams.write");
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{t("title")}</h1>
          <p className="max-w-3xl text-sm text-muted-foreground">{t("subtitle")}</p>
        </div>
        {write && <Button onClick={() => setEditing("new")} data-testid="team-new"><Plus aria-hidden />{t("new")}</Button>}
      </div>
      {teams.isLoading && <Skeleton className="h-40" aria-busy="true" />}
      {teams.data?.length === 0 && (
        <Card className="flex flex-col items-center gap-3 p-10 text-center"><Users className="size-8 text-muted-foreground" aria-hidden /><p className="text-muted-foreground">{t("empty")}</p></Card>
      )}
      <ul className="grid gap-3 md:grid-cols-2" aria-label={t("title")}>
        {teams.data?.map((team) => (
          <li key={team.id}>
            <Card className="flex h-full flex-col gap-2 p-4" data-testid={`team-${team.id}`}>
              <h2 className="text-base font-semibold" dir="auto">{team.name}</h2>
              {team.description && <p className="text-sm text-muted-foreground" dir="auto">{team.description}</p>}
              <p className="text-xs text-muted-foreground">{t("counts", { agents: team.agent_ids.length, users: team.user_ids.length, projects: team.project_ids.length })}</p>
              <ul className="flex flex-wrap gap-1.5">
                {team.agent_ids.map((id) => (
                  <li key={id} className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs">
                    {id === team.lead_agent_id && <Crown className="size-3 text-warning" aria-label={t("lead")} />}<span dir="auto">{agentName(id)}</span>
                  </li>
                ))}
              </ul>
              {write && (
                <div className="mt-auto flex gap-2 pt-2">
                  <Button size="sm" variant="outline" onClick={() => setEditing(team)} aria-label={`${t("edit")}: ${team.name}`}>{t("edit")}</Button>
                  <Button size="sm" variant="outline" onClick={() => setDeleting(team)} aria-label={`${t("delete")}: ${team.name}`}><Trash2 aria-hidden />{t("delete")}</Button>
                </div>
              )}
            </Card>
          </li>
        ))}
      </ul>
      {editing && <TeamDialog team={editing === "new" ? undefined : editing} onClose={() => setEditing(undefined)} />}
      <ConfirmDialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(undefined)} destructive title={t("deleteTitle")}
        description={t("deleteBody", { name: deleting?.name ?? "" })} confirmLabel={t("delete")} busy={a.remove.isPending}
        onConfirm={() => deleting && a.remove.mutate(deleting.id, {
          onSuccess: () => setDeleting(undefined),
          onError: (e) => toast("error", tc("requestFailed", { code: e instanceof ApiError ? e.code : "error" })),
        })} />
    </div>
  );
}

function TeamDialog({ team, onClose }: { team?: Team; onClose: () => void }) {
  const t = useTranslations("teams");
  const tc = useTranslations("common");
  const can = useCan();
  const agents = useAgents({});
  const users = useUsers(can("users.manage"));
  const a = useTeamActions();
  const toast = useToasts((s) => s.push);
  const [name, setName] = useState(team?.name ?? "");
  const [description, setDescription] = useState(team?.description ?? "");
  const [agentIds, setAgentIds] = useState<number[]>(team?.agent_ids ?? []);
  const [userIds, setUserIds] = useState<number[]>(team?.user_ids ?? []);
  const [lead, setLead] = useState<string>(team?.lead_agent_id ? String(team.lead_agent_id) : "");
  const fail = (e: unknown) => {
    const code = e instanceof ApiError ? e.code : "error";
    toast("error", t.has(`errors.${code}`) ? t(`errors.${code}`) : tc("requestFailed", { code }));
  };
  const body = { name: name.trim(), description, agent_ids: agentIds, user_ids: userIds,
    lead_agent_id: lead && agentIds.includes(Number(lead)) ? Number(lead) : null, policies: team?.policies ?? {} };
  const busy = a.create.isPending || a.update.isPending;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")} className="max-h-[90vh] max-w-xl overflow-y-auto">
        <DialogTitle>{team ? t("editTitle", { name: team.name }) : t("new")}</DialogTitle>
        <DialogDescription>{t("help")}</DialogDescription>
        <form className="mt-4 flex flex-col gap-4" onSubmit={(e) => {
          e.preventDefault();
          if (team) a.update.mutate({ id: team.id, ...body }, { onSuccess: onClose, onError: fail });
          else a.create.mutate(body, { onSuccess: onClose, onError: fail });
        }}>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="t-name">{t("name")}</Label>
            <Input id="t-name" required maxLength={128} value={name} onChange={(e) => setName(e.target.value)} dir="auto" />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="t-desc">{t("description")}</Label>
            <Textarea id="t-desc" rows={2} maxLength={4000} value={description} onChange={(e) => setDescription(e.target.value)} dir="auto" />
          </div>
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-sm font-medium">{t("agents")}</legend>
            <div className="grid gap-2 sm:grid-cols-2">
              {agents.data?.map((ag) => (
                <label key={ag.id} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" className="size-4" checked={agentIds.includes(ag.id)} onChange={(e) => setAgentIds(e.target.checked ? [...agentIds, ag.id] : agentIds.filter((x) => x !== ag.id))} />
                  <span dir="auto">{ag.name}</span>
                </label>
              ))}
            </div>
          </fieldset>
          <div className="flex max-w-xs flex-col gap-1.5">
            <Label htmlFor="t-lead">{t("lead")}</Label>
            <NativeSelect id="t-lead" value={lead} onChange={(e) => setLead(e.target.value)}>
              <option value="">{tc("none")}</option>
              {agents.data?.filter((ag) => agentIds.includes(ag.id)).map((ag) => <option key={ag.id} value={ag.id}>{ag.name}</option>)}
            </NativeSelect>
          </div>
          {users.data && (
            <fieldset className="flex flex-col gap-2">
              <legend className="mb-1 text-sm font-medium">{t("people")}</legend>
              <div className="grid gap-2 sm:grid-cols-2">
                {users.data.map((u) => (
                  <label key={u.id} className="flex items-center gap-2 text-sm">
                    <input type="checkbox" className="size-4" checked={userIds.includes(u.id)} onChange={(e) => setUserIds(e.target.checked ? [...userIds, u.id] : userIds.filter((x) => x !== u.id))} />
                    <span dir="ltr">{u.username}</span>
                  </label>
                ))}
              </div>
            </fieldset>
          )}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>{tc("cancel")}</Button>
            <Button type="submit" disabled={busy} data-testid="team-save">{busy ? tc("saving") : tc("save")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
