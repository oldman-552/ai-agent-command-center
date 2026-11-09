"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

export const BOARD_COLUMNS = ["BACKLOG", "PLANNED", "READY", "IN_PROGRESS", "BLOCKED", "REVIEW", "APPROVAL", "COMPLETED", "FAILED"] as const;
export const ALL_STATUSES = [...BOARD_COLUMNS, "CANCELLED"] as const;

export type Evidence =
  | { kind: "execution"; execution_id: string; agent_id: number; is_mock: boolean; output_sha256: string; at: string }
  | { kind: "review"; decision: "approve" | "revise" | "reject"; by: string; note: string; at: string };

export type Task = {
  id: number; project_id: number | null; workflow_id: number | null; title: string; description: string;
  task_type: string; required_skills: string[]; status: string; priority: number; assigned_agent_id: number | null;
  acceptance_criteria: string[]; evidence: Evidence[]; review_status: string;
  result: { output?: string; execution_id?: string; review_notes?: string[] } | null; error: string | null;
  attempts: number; max_attempts: number; budget_usd: string | null; estimated_effort: string | null; started_at: string | null; finished_at: string | null;
  created_at: string; updated_at: string;
};
export type TaskDetail = Task & {
  depends_on: number[];
  executions: { id: string; status: string; agent_id: number | null; is_mock: boolean; created_at: string; ended_at: string | null }[];
};
export type Project = { id: number; name: string; description: string; mode: string; agent_ids: number[]; created_at: string };
export type Workflow = {
  id: number; project_id: number; title: string; objective: string; status: string; error: string | null;
  plan: { summary?: string; planner?: { is_mock: boolean }; rounds?: { dispatched: number[]; waiting: Record<string, string> }[] } | null;
  created_at: string; updated_at: string;
  tasks?: Task[]; dependencies?: { task_id: number; depends_on_id: number }[];
};

const ACTIVE = new Set(["planning", "running"]);

export const useProjects = () => useQuery({ queryKey: ["projects"], queryFn: () => api<Project[]>("/api/projects") });

export const useWorkflows = (projectId?: number) =>
  useQuery({
    queryKey: ["workflows", projectId ?? null],
    queryFn: () => api<Workflow[]>(`/api/workflows${projectId ? `?project_id=${projectId}` : ""}`),
    refetchInterval: 5_000,
  });

export const useWorkflow = (id?: number) =>
  useQuery({
    queryKey: ["workflow", id],
    queryFn: () => api<Workflow>(`/api/workflows/${id}`),
    enabled: !!id,
    refetchInterval: (q) => (q.state.data && ACTIVE.has(q.state.data.status) ? 2_000 : false),
  });

export const useTasks = (params: { projectId?: number; workflowId?: number }) =>
  useQuery({
    queryKey: ["tasks", params],
    queryFn: () => {
      const sp = new URLSearchParams();
      if (params.projectId) sp.set("project_id", String(params.projectId));
      if (params.workflowId) sp.set("workflow_id", String(params.workflowId));
      return api<Task[]>(`/api/tasks?${sp}`);
    },
    refetchInterval: 3_000,
  });

export const useTask = (id?: number) =>
  useQuery({ queryKey: ["task", id], queryFn: () => api<TaskDetail>(`/api/tasks/${id}`), enabled: !!id, refetchInterval: 3_000 });

function useInvalidate() {
  const qc = useQueryClient();
  return () => ["tasks", "task", "workflow", "workflows", "projects"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
}

export function useCreateProject() {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (body: { name: string; description: string; agent_ids: number[] }) =>
      api<Project>("/api/projects", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: inv,
  });
}

export function useSubmitObjective() {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: (body: { project_id: number; title: string; objective: string }) =>
      api<Workflow>("/api/workflows", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: inv,
  });
}

export function useReview() {
  const inv = useInvalidate();
  return useMutation({
    mutationFn: ({ id, decision, note }: { id: number; decision: "approve" | "revise" | "reject"; note: string }) =>
      api<Task>(`/api/tasks/${id}/review`, { method: "POST", body: JSON.stringify({ decision, note }) }),
    onSuccess: inv,
  });
}
