"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

export const MEMORY_TYPES = ["project", "task", "technical_decision", "codebase", "execution", "lesson", "conversation", "agent_identity", "team_shared"] as const;
export const PROJECT_TYPES = ["project", "task", "technical_decision", "codebase", "execution", "lesson"] as const;
export type Verification = "verified" | "assumption" | "suggestion";

export type Memory = {
  id: number; memory_type: string; title: string; content: string; verification: Verification; version: number;
  source: { kind?: string; ref?: string; execution_id?: string; corrects?: number; verified_by?: string; [k: string]: unknown };
  supersedes_id: number | null; is_current: boolean; project_id: number | null; agent_id: number | null;
  team_id: number | null; task_id: number | null; expires_at: string | null; created_at: string; updated_at: string;
  score?: number;
};
export type MemoryDetail = Memory & { history: Memory[] };
export type Segment = { title: string; tokens: number; trusted: boolean; source?: Record<string, unknown> };
export type ContextPreview = {
  task_id: number; agent_id: number | null; system: string; prompt: string;
  manifest: {
    budget: number; task_type: string; estimated_tokens: number; segments: Record<string, Segment[]>;
    dropped: { title: string; kind: string; reason: string }[]; compressed: { title: string; kind: string; from: number; to: number }[];
    flagged: string[];
  };
};

const fetchList = (projectId: number, type?: string, verification?: string) => {
  const sp = new URLSearchParams({ project_id: String(projectId) });
  if (type) sp.set("memory_type", type);
  if (verification) sp.set("verification", verification);
  return api<Memory[]>(`/api/memory?${sp}`);
};
const fetchSearch = (projectId: number, q: string) =>
  api<Memory[]>(`/api/memory/search?${new URLSearchParams({ project_id: String(projectId), q, limit: "20" })}`);
const fetchDetail = (id: number) => api<MemoryDetail>(`/api/memory/${id}`);
const fetchPreview = (taskId: number) => api<ContextPreview>(`/api/context/tasks/${taskId}`);

export const useMemories = (projectId: number | undefined, f: { q?: string; type?: string; verification?: string }) =>
  useQuery({
    queryKey: ["memories", projectId, f],
    queryFn: () => (f.q ? fetchSearch(projectId!, f.q) : fetchList(projectId!, f.type, f.verification)),
    enabled: !!projectId,
  });

export const useMemory = (id?: number) => useQuery({ queryKey: ["memory", id], queryFn: () => fetchDetail(id!), enabled: !!id });

export const useContextPreview = (taskId?: number) =>
  useQuery({ queryKey: ["context", taskId], queryFn: () => fetchPreview(taskId!), enabled: !!taskId });

export type MemoryInput = { memory_type: string; title: string; content: string; project_id: number };

export function useMemoryActions() {
  const qc = useQueryClient();
  const done = () => { qc.invalidateQueries({ queryKey: ["memories"] }); qc.invalidateQueries({ queryKey: ["memory"] }); qc.invalidateQueries({ queryKey: ["context"] }); };
  return {
    create: useMutation({ mutationFn: (b: MemoryInput) => api<Memory>("/api/memory", { method: "POST", body: JSON.stringify(b) }), onSuccess: done }),
    correct: useMutation({
      mutationFn: ({ id, ...b }: { id: number; title?: string; content: string }) =>
        api<Memory>(`/api/memory/${id}/correct`, { method: "POST", body: JSON.stringify(b) }),
      onSuccess: done,
    }),
    verify: useMutation({ mutationFn: (id: number) => api<Memory>(`/api/memory/${id}/verify`, { method: "POST" }), onSuccess: done }),
    remove: useMutation({ mutationFn: (id: number) => api<{ deleted: number }>(`/api/memory/${id}`, { method: "DELETE" }), onSuccess: done }),
  };
}

export type EmbeddingState = { enabled: boolean; provider_id: number | null; model: string | null; pending: number;
  supported_kinds: string[]; fallback: string };
export const useEmbedding = () => useQuery({ queryKey: ["memory-embedding"], queryFn: () => api<EmbeddingState>("/api/memory/embedding") });
export function useEmbeddingActions() {
  const qc = useQueryClient();
  const done = () => qc.invalidateQueries({ queryKey: ["memory-embedding"] });
  return {
    save: useMutation({ mutationFn: (b: { provider_id: number | null; model?: string | null }) =>
      api<EmbeddingState>("/api/memory/embedding", { method: "PUT", body: JSON.stringify(b) }), onSuccess: done }),
    reindex: useMutation({ mutationFn: () =>
      api<EmbeddingState & { indexed: number; failed: number }>("/api/memory/embedding/reindex", { method: "POST" }), onSuccess: done }),
  };
}
