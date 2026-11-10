"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

const send = <T,>(path: string, body?: unknown, method = "POST") =>
  api<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });

function useInvalidating<A, R>(keys: string[], fn: (a: A) => Promise<R>) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: fn, onSuccess: () => keys.forEach((k) => qc.invalidateQueries({ queryKey: [k] })) });
}

// ---------------- projects ----------------
export type ProjectFull = {
  id: number; name: string; description: string; mode: "new" | "existing" | "maintenance"; agent_ids: number[];
  team_id: number | null; budget_usd: string | null; repository_id: number | null; analyzed_at: string | null; created_at: string;
};
export type Analysis = {
  analyzed_at: string; files: number; truncated: boolean;
  technologies: { languages: { name: string; files: number; lines: number }[]; frameworks: string[]; infrastructure: string[] };
  architecture: { dir: string; role: string; files: number }[];
  dependencies: { python: Record<string, string>; node: Record<string, string>; go: Record<string, string>; lockfiles: string[] };
  tests: { files: number; frameworks: string[]; sample: string[] };
  large_files: { file: string; lines: number }[];
  debt: { kind: string; detail: string }[];
};
const getProjects = () => api<ProjectFull[]>("/api/projects");
const getAnalysis = (id: number) => api<Analysis>(`/api/projects/${id}/analysis`);
export const useProjectsFull = () => useQuery({ queryKey: ["projects"], queryFn: getProjects });
export const useAnalysis = (id?: number, enabled = true) =>
  useQuery({ queryKey: ["analysis", id], queryFn: () => getAnalysis(id!), enabled: !!id && enabled, retry: false });
export function useProjectActions() {
  const keys = ["projects", "analysis"];
  return {
    create: useInvalidating(keys, (b: { name: string; description: string; mode: string; agent_ids: number[] }) => send<ProjectFull>("/api/projects", b)),
    update: useInvalidating(keys, ({ id, ...b }: { id: number; description?: string; mode?: string; team_id?: number | null; budget_usd?: string | null; agent_ids?: number[] }) =>
      send<ProjectFull>(`/api/projects/${id}`, b, "PATCH")),
    importRepo: useInvalidating(keys, (id: number) => send<{ head: string }>(`/api/projects/${id}/import`)),
    analyze: useInvalidating(keys, (id: number) => send<Analysis>(`/api/projects/${id}/analyze`)),
  };
}

// ---------------- teams ----------------
export type Team = {
  id: number; name: string; description: string; lead_agent_id: number | null; agent_ids: number[]; user_ids: number[]; policies: Record<string, unknown>;
  project_ids: number[]; created_at: string;
};
const getTeams = () => api<Team[]>("/api/teams");
export const useTeams = (enabled = true) => useQuery({ queryKey: ["teams"], queryFn: getTeams, enabled });
export function useTeamActions() {
  const keys = ["teams", "projects"];
  return {
    create: useInvalidating(keys, (b: Omit<Team, "id" | "project_ids" | "created_at">) => send<Team>("/api/teams", b)),
    update: useInvalidating(keys, ({ id, ...b }: Partial<Team> & { id: number }) => send<Team>(`/api/teams/${id}`, b, "PATCH")),
    remove: useInvalidating(keys, (id: number) => send<void>(`/api/teams/${id}`, undefined, "DELETE")),
  };
}

// ---------------- budgets & analytics ----------------
export type BudgetRow = {
  id: number; enabled: boolean; budget_id: number; scope: string; scope_id: number | null; period: string;
  limit_usd: string | null; limit_tokens: number | null; spent_usd: string; spent_tokens: number; exceeded: boolean;
};
export type UsageItem = {
  key: string | null; label: string; is_mock: boolean; requests: number; input_tokens: number; output_tokens: number;
  cached_tokens: number; cost_usd: string | null; unpriced_requests: number; avg_duration_ms: number | null; retries: number;
};
export type Usage = { group: string; days: number; items: UsageItem[]; totals: { requests: number; tokens: number; cost_usd: string; unpriced_requests: number } };
const getBudgets = () => api<BudgetRow[]>("/api/budgets");
const getUsage = (group: string, days: number) => api<Usage>(`/api/analytics/usage?${new URLSearchParams({ group, days: String(days) })}`);
export const useBudgets = () => useQuery({ queryKey: ["budgets"], queryFn: getBudgets });
export const useUsage = (group: string, days: number) => useQuery({ queryKey: ["usage", group, days], queryFn: () => getUsage(group, days) });
export function useBudgetActions() {
  const keys = ["budgets"];
  return {
    create: useInvalidating(keys, (b: { scope: string; scope_id: number | null; period: string; limit_usd?: string; limit_tokens?: number }) => send<BudgetRow>("/api/budgets", b)),
    update: useInvalidating(keys, ({ id, ...b }: { id: number; enabled?: boolean; limit_usd?: string; limit_tokens?: number }) => send<BudgetRow>(`/api/budgets/${id}`, b, "PATCH")),
    remove: useInvalidating(keys, (id: number) => send<void>(`/api/budgets/${id}`, undefined, "DELETE")),
  };
}

// ---------------- runtime & executions ----------------
export type RuntimeOverview = {
  engines: { kind: string; is_mock: boolean; agents: number }[]; executions: Record<string, number>;
  circuits: { scope: string; ref_id: number; state: string; failures: number; last_failure: string | null; opened_at: string | null; updated_at: string }[];
};
export type Execution = {
  id: string; agent_id: number | null; project_id: number | null; task_id: number | null; provider: string | null; model: string | null;
  engine: string; status: string; started_at: string | null; ended_at: string | null; output: { text?: string } | null;
  input_tokens: number; output_tokens: number; cost_usd: string | null; error: string | null; error_category: string | null;
  attempt: number; is_mock: boolean; created_at: string;
};
export type ExecEvent = { seq: number; ts: string; type: string; payload: Record<string, unknown> };
const getRuntime = () => api<RuntimeOverview>("/api/runtime/overview");
const getExecutions = (status?: string) => api<Execution[]>(`/api/executions?${new URLSearchParams(status ? { status, limit: "100" } : { limit: "100" })}`);
const getExecution = (id: string) => api<Execution>(`/api/executions/${id}`);
const getEvents = (id: string) => api<ExecEvent[]>(`/api/executions/${id}/events`);
export const useRuntime = () => useQuery({ queryKey: ["runtime"], queryFn: getRuntime, refetchInterval: 10_000 });
export const useExecutions = (status?: string) => useQuery({ queryKey: ["executions", status], queryFn: () => getExecutions(status) });
export const useExecution = (id?: string) => useQuery({ queryKey: ["execution", id], queryFn: () => getExecution(id!), enabled: !!id });
export const useExecEvents = (id?: string, live = false) =>
  useQuery({ queryKey: ["exec-events", id], queryFn: () => getEvents(id!), enabled: !!id, refetchInterval: live ? 1_500 : false });

// ---------------- users & A2A keys ----------------
export type UserRow = { id: number; username: string; role: string; is_active: boolean; language: string; created_at?: string };
export type RoleRow = { key: string; name_ar: string; name_en: string; permissions: string[] };
export type A2AKey = { id: number; name: string; hint: string; agent_ids: number[]; created_at: string; last_used_at: string | null; revoked_at: string | null };
const getUsers = () => api<UserRow[]>("/api/users");
const getRoles = () => api<RoleRow[]>("/api/roles");
const getA2A = () => api<A2AKey[]>("/api/a2a/keys");
export const useUsers = (enabled: boolean) => useQuery({ queryKey: ["users"], queryFn: getUsers, enabled });
export const useRoles = (enabled: boolean) => useQuery({ queryKey: ["roles"], queryFn: getRoles, enabled });
export const useA2AKeys = (enabled: boolean) => useQuery({ queryKey: ["a2a-keys"], queryFn: getA2A, enabled });
export function useAdminActions() {
  return {
    createUser: useInvalidating(["users"], (b: { username: string; password: string; role: string; language: string }) => send<UserRow>("/api/users", b)),
    patchUser: useInvalidating(["users"], ({ id, ...b }: { id: number; role?: string; is_active?: boolean }) => send<UserRow>(`/api/users/${id}`, b, "PATCH")),
    createA2A: useInvalidating(["a2a-keys"], (b: { name: string; agent_ids: number[] }) => send<A2AKey & { token: string }>("/api/a2a/keys", b)),
    revokeA2A: useInvalidating(["a2a-keys"], (id: number) => send<A2AKey>(`/api/a2a/keys/${id}`, undefined, "DELETE")),
  };
}
