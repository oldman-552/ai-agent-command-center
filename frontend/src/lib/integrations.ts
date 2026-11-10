"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type Finding = { category: string; label: string; file: string; line: number; excerpt: string };
export type Assessment = { risk: "low" | "medium" | "high"; summary: Record<string, number>; findings: Finding[]; files_scanned: number; truncated: boolean };
export type McpTool = { id: number; name: string; description: string; permission_key: string; enabled: boolean; input_schema: Record<string, unknown> };
export type McpServer = {
  id: number; name: string; transport: "stdio" | "http"; command: string[] | null; url: string | null; has_credential: boolean;
  enabled: boolean; status: string; assessment: Assessment | null; resources: { uri: string; name: string }[];
  last_checked_at: string | null; last_error: string | null; created_at: string; tools?: McpTool[];
};
export type Skill = {
  id: number; key: string; name: string; source: string; version: string | null; license: string | null; instructions: string;
  assessment: Assessment | null; enabled: boolean; agent_ids: number[]; created_at: string;
};
export type LocalSkill = { local: string; name: string; description: string };
export type Inspection = { source: string; name: string | null; description: string | null; license: string | null; files: string[]; assessment: Assessment };
export type McpKey = { id: number; name: string; hint: string; tools: string[]; created_at: string; last_used_at: string | null; revoked_at: string | null };
export type McpKeys = { keys: McpKey[]; tools: string[]; browser_tools: string[]; endpoint: string };

const getServers = () => api<McpServer[]>("/api/integrations/mcp-servers");
const getServer = (id: number) => api<McpServer>(`/api/integrations/mcp-servers/${id}`);
const getSkills = () => api<Skill[]>("/api/integrations/skills");
const getLocal = () => api<LocalSkill[]>("/api/integrations/skills/available");
const getKeys = () => api<McpKeys>("/api/integrations/mcp-keys");
const post = <T,>(path: string, body?: unknown, method = "POST") => api<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });

export const useServers = () => useQuery({ queryKey: ["mcp-servers"], queryFn: getServers });
export const useServer = (id?: number) => useQuery({ queryKey: ["mcp-server", id], queryFn: () => getServer(id!), enabled: !!id });
export const useSkills = () => useQuery({ queryKey: ["skills"], queryFn: getSkills });
export const useLocalSkills = (enabled: boolean) => useQuery({ queryKey: ["skills-local"], queryFn: getLocal, enabled });
export const useMcpKeys = (enabled: boolean) => useQuery({ queryKey: ["mcp-keys"], queryFn: getKeys, enabled });

export function useIntegrationActions() {
  const qc = useQueryClient();
  const done = () => ["mcp-servers", "mcp-server", "skills", "mcp-keys"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const m = <A, R>(fn: (a: A) => Promise<R>) => useMutation({ mutationFn: fn, onSuccess: done });
  return {
    register: m((b: { name: string; transport: string; command?: string[]; url?: string; token?: string }) => post<McpServer>("/api/integrations/mcp-servers", b)),
    discover: m((id: number) => post<McpServer>(`/api/integrations/mcp-servers/${id}/discover`)),
    health: m((id: number) => post<{ status: string; latency_ms: number }>(`/api/integrations/mcp-servers/${id}/health`)),
    enableServer: m(({ id, enabled, accept_risks }: { id: number; enabled: boolean; accept_risks?: boolean }) =>
      post<McpServer>(`/api/integrations/mcp-servers/${id}/enabled`, { enabled, accept_risks: !!accept_risks }, "PUT")),
    enableTool: m(({ server, tool, enabled }: { server: number; tool: number; enabled: boolean }) =>
      post<McpTool>(`/api/integrations/mcp-servers/${server}/tools/${tool}`, { enabled }, "PUT")),
    deleteServer: m((id: number) => post<void>(`/api/integrations/mcp-servers/${id}`, undefined, "DELETE")),
    inspect: useMutation({ mutationFn: (local: string) => post<Inspection>("/api/integrations/skills/inspect", { local }) }),
    install: m((local: string) => post<Skill>("/api/integrations/skills", { local })),
    enableSkill: m(({ id, enabled, accept_risks }: { id: number; enabled: boolean; accept_risks?: boolean }) =>
      post<Skill>(`/api/integrations/skills/${id}/enabled`, { enabled, accept_risks: !!accept_risks }, "PUT")),
    assignSkill: m(({ id, agent_ids }: { id: number; agent_ids: number[] }) => post<Skill>(`/api/integrations/skills/${id}/agents`, { agent_ids }, "PUT")),
    deleteSkill: m((id: number) => post<void>(`/api/integrations/skills/${id}`, undefined, "DELETE")),
    createKey: m((b: { name: string; tools: string[] }) => post<McpKey & { token: string }>("/api/integrations/mcp-keys", b)),
    revokeKey: m((id: number) => post<McpKey>(`/api/integrations/mcp-keys/${id}`, undefined, "DELETE")),
  };
}
