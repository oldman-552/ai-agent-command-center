"use client";
import { useInfiniteQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type AuditEntry = {
  id: number; ts: string; actor: string; action: string; category: string; level: string; message: string;
  details: Record<string, unknown>; user_id: number | null; agent_id: number | null; project_id: number | null;
  task_id: number | null; execution_id: string | null; tokens: number; cost_usd: string | null;
};
export type AuditPage = { items: AuditEntry[]; total: number; next_before_id: number | null; categories: string[] };
export type AuditFilters = { q?: string; category?: string; level?: string; actor?: string; since?: string; until?: string };

export function auditQuery(f: AuditFilters) {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(f)) if (v) sp.set(k, v);
  return sp;
}

export const useAudit = (f: AuditFilters) =>
  useInfiniteQuery({
    queryKey: ["audit", f],
    initialPageParam: null as number | null,
    queryFn: ({ pageParam }) => {
      const sp = auditQuery(f);
      sp.set("limit", "50");
      if (pageParam) sp.set("before_id", String(pageParam));
      return api<AuditPage>(`/api/audit?${sp}`);
    },
    getNextPageParam: (last) => last.next_before_id,
  });
