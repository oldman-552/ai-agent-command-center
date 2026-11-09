"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type Agent = {
  id: number; identifier: string; name: string; description: string; role: string; avatar: string | null;
  template_id: number | null; system_instructions: string; provider_id: number | null; model: string | null;
  fallback_provider_id: number | null; fallback_model: string | null; execution_engine: "api" | "cli" | "browser" | "a2a";
  tool_permissions: string[]; skills: string[]; mcp_server_ids: number[]; memory_profile: { scopes?: string[] };
  context_policy: { task_types?: string[] }; security_profile: "read_only" | "reviewer" | "developer" | "manager";
  project_ids: number[]; token_budget: number | null; cost_budget_usd: string | null; timeout_seconds: number;
  retry_policy: { max_attempts: number; backoff_seconds: number; backoff_multiplier: number };
  concurrency_limit: number; priority: number; supervisor_agent_id: number | null; state: string; is_enabled: boolean;
  archived_at: string | null; config_version: number; last_error: string | null; created_at: string; updated_at: string;
};
export type Template = {
  id: number; key: string; name_ar: string; name_en: string; role: string; description_ar: string; description_en: string;
  tool_permissions: string[]; security_profile: string; is_system: boolean;
};
export type ProviderLite = { id: number; name: string; kind: string; is_mock: boolean; enabled: boolean; models: { name: string }[] };
export type ToolCatalog = { tools: Record<string, string>; profile_ceiling: Record<string, string[]> };

export const useAgents = (params: { q?: string; state?: string; archived?: boolean }) =>
  useQuery({
    queryKey: ["agents", params],
    queryFn: () => {
      const sp = new URLSearchParams();
      if (params.q) sp.set("q", params.q);
      if (params.state) sp.set("state", params.state);
      if (params.archived) sp.set("archived", "true");
      return api<Agent[]>(`/api/agents?${sp}`);
    },
    refetchInterval: 10_000,
  });
export const useAgent = (id: number) => useQuery({ queryKey: ["agent", id], queryFn: () => api<Agent>(`/api/agents/${id}`) });
export const useTemplates = () => useQuery({ queryKey: ["templates"], queryFn: () => api<Template[]>("/api/agent-templates") });
export const useProvidersLite = () => useQuery({ queryKey: ["providers-lite"], queryFn: () => api<ProviderLite[]>("/api/providers") });
export const useToolCatalog = () => useQuery({ queryKey: ["tools"], queryFn: () => api<ToolCatalog>("/api/tools"), staleTime: Infinity });

export function useAgentAction() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, action }: { id: number; action: string }): Promise<Agent | null> => {
      if (action === "delete") {
        await api<void>(`/api/agents/${id}`, { method: "DELETE" });
        return null;
      }
      return api<Agent>(`/api/agents/${id}/${action}`, { method: "POST" });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agents"] });
      qc.invalidateQueries({ queryKey: ["agent"] });
    },
  });
}
