"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type ApprovalEvidence = { kind: string; [k: string]: unknown };
export type Approval = {
  id: number; operation: string; operation_label: string; risk_level: "low" | "medium" | "high" | "critical";
  status: "pending" | "approved" | "rejected" | "changes_requested" | "executed" | "expired";
  title: string; summary: string; payload: Record<string, unknown>; evidence: ApprovalEvidence[];
  agent_id: number | null; project_id: number | null; task_id: number | null; execution_id: string | null;
  requested_by: string; approver_id: number | null; decided_at: string | null; reason: string | null;
  executed_at: string | null; created_at: string; executable: boolean;
};
export type ApprovalList = { items: Approval[]; counts: Record<string, number> };

export const useApprovals = (params: { status?: string; risk?: string }, enabled = true) =>
  useQuery({
    queryKey: ["approvals", params],
    queryFn: () => {
      const sp = new URLSearchParams();
      if (params.status) sp.set("status", params.status);
      if (params.risk) sp.set("risk", params.risk);
      return api<ApprovalList>(`/api/approvals?${sp}`);
    },
    enabled,
    refetchInterval: 10_000,
  });

export function useDecide() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, decision, reason }: { id: number; decision: "approve" | "reject" | "changes_requested"; reason: string }) =>
      api<Approval>(`/api/approvals/${id}/decide`, { method: "POST", body: JSON.stringify({ decision, reason }) }),
    onSuccess: () => ["approvals", "tasks", "task", "workflow", "wf-task", "wf-tasks", "wf-objective", "wf-dashboard"]
      .forEach((k) => qc.invalidateQueries({ queryKey: [k] })),
  });
}
