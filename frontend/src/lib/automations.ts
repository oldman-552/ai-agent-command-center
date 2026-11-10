"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { Workflow } from "@/lib/tasks";

export type Trigger = { type: "cron"; expr: string } | { type: "interval"; minutes: number } | { type: "event"; action: string };
export type Action = { type: "workflow"; title: string; objective: string } | { type: "analyze_project" };
export type Automation = {
  id: number; name: string; project_id: number; trigger: Trigger; action: Action; status: "active" | "paused";
  last_run_at: string | null; next_run_at: string | null; created_at: string;
};
export type AutomationRun = { id: number; trigger: string; status: string; detail: string; result: Record<string, unknown>; started_at: string; finished_at: string | null };

const send = <T,>(path: string, body?: unknown, method = "POST") => api<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
const getAll = () => api<Automation[]>("/api/automations");
const getRuns = (id: number) => api<AutomationRun[]>(`/api/automations/${id}/runs`);
const getWorkflows = () => api<Workflow[]>("/api/workflows?limit=100");
export const useAutomations = () => useQuery({ queryKey: ["automations"], queryFn: getAll });
export const useAutomationRuns = (id?: number) => useQuery({ queryKey: ["automation-runs", id], queryFn: () => getRuns(id!), enabled: !!id });
export const useAllWorkflows = () => useQuery({ queryKey: ["workflows-all"], queryFn: getWorkflows, refetchInterval: 10_000 });

export function useAutomationActions() {
  const qc = useQueryClient();
  const done = () => ["automations", "automation-runs", "workflows-all"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const m = <A, R>(fn: (a: A) => Promise<R>) => useMutation({ mutationFn: fn, onSuccess: done });
  return {
    create: m((b: { name: string; project_id: number; trigger: Trigger; action: Action }) => send<Automation>("/api/automations", b)),
    edit: m(({ id, ...b }: { id: number; name?: string; trigger?: Trigger; action?: Action }) => send<Automation>(`/api/automations/${id}`, b, "PATCH")),
    pause: m((id: number) => send<Automation>(`/api/automations/${id}/pause`)),
    resume: m((id: number) => send<Automation>(`/api/automations/${id}/resume`)),
    run: m((id: number) => send<AutomationRun>(`/api/automations/${id}/run`)),
    remove: m((id: number) => send<void>(`/api/automations/${id}`, undefined, "DELETE")),
  };
}
