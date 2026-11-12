"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

// ---------- types (the shapes of /api/workforce) ----------
export type Mode = "AUTOMATIC" | "HUMAN_ASSISTED" | "DISABLED";
export type Lifecycle =
  | "DRAFT" | "PLANNED" | "READY" | "ASSIGNED" | "WAITING_FOR_ACCEPTANCE" | "ACCEPTED" | "QUEUED" | "RUNNING"
  | "SUBMITTED" | "UNDER_REVIEW" | "APPROVED" | "REJECTED" | "REVISION_REQUESTED" | "COMPLETED" | "FAILED"
  | "BLOCKED" | "CANCELLED";
export const LIFECYCLES: Lifecycle[] = [
  "PLANNED", "READY", "ASSIGNED", "WAITING_FOR_ACCEPTANCE", "ACCEPTED", "QUEUED", "RUNNING", "SUBMITTED",
  "UNDER_REVIEW", "APPROVED", "REVISION_REQUESTED", "COMPLETED", "REJECTED", "FAILED", "BLOCKED", "CANCELLED",
];
/** Board columns: every lifecycle state belongs to exactly one. */
export const BOARD: { key: string; states: Lifecycle[] }[] = [
  { key: "waiting", states: ["DRAFT", "PLANNED", "READY", "BLOCKED", "REVISION_REQUESTED"] },
  { key: "people", states: ["WAITING_FOR_ACCEPTANCE", "ACCEPTED"] },
  { key: "automatic", states: ["ASSIGNED", "QUEUED", "RUNNING"] },
  { key: "review", states: ["SUBMITTED", "UNDER_REVIEW", "APPROVED"] },
  { key: "done", states: ["COMPLETED"] },
  { key: "stopped", states: ["REJECTED", "FAILED", "CANCELLED"] },
];
export type ReportClass = "verified" | "human_approved" | "worker_proposal" | "unverified_claim" | "failed" | "incomplete";

export type Binding = {
  type: "deterministic_session" | "deterministic_single_account" | "pooled" | "unknown" | "unconfigured" | "not_applicable"
    | "browser_profile";
  attested: false; account?: string | null; account_hash?: string | null; account_usable?: boolean | null;
  session?: string | null; session_state?: string | null; session_model?: string | null; checked_at?: string | null;
  /** ADR-035: the worker drives its own visible browser window (a profile of its own, signed in by hand). */
  engine?: "browser"; session_id?: number; site?: string; profile?: string; state?: string; error?: string | null;
};
export type Worker = {
  id: number; slot: number; label: string; agent_id: number; identifier: string; name: string; role: string;
  description: string | null; skills: string[]; prompt: string; mode: Mode; fallback_to_human: boolean;
  provider: { id: number; name: string; kind: string; is_mock: boolean; enabled: boolean; is_bridge: boolean;
    terms_acknowledged: boolean | null; health_fresh: boolean | null } | null;
  model: string | null; timeout_seconds: number;
  retry_policy: { max_attempts: number; backoff_seconds: number; backoff_multiplier: number };
  concurrency_limit: number; account_label: string | null; account_binding: Binding;
  status: { state: "ready" | "human" | "disabled" | "fallback" | "unavailable"; reason: string | null };
  assignee: { id: number; username: string } | null; busy_tasks: number; shares_session_with: number[];
  is_enabled: boolean; updated_at: string;
};
export type Route = { provider_id: number; provider: string; kind: string; model: string; is_mock: boolean; enabled: boolean };
export type Manager = {
  configured: boolean; problem: string | null; agent_id: number | null; name?: string; model?: Route | null;
  fallback?: Route | null; instructions?: string; timeout_seconds?: number; is_mock?: boolean;
  engine?: string; via_bridge?: boolean;
  window?: { session_id: number; site: string; profile: string; state: string; error: string | null } | null;
};
export type Development = { enabled: boolean; test_command: string[] | null; timeout_seconds: number;
  allowed_test_commands: string[] };
export type Workforce = {
  id: number; name: string; project_id: number | null; review_policy: "manager" | "human"; max_revisions: number;
  manager: Manager; workers: Worker[]; development: Development; max_workers?: number;
  accounts: { automatic_workers: number; deterministic: number; pooled: number; unknown: number;
    distinct_accounts: number; shared_accounts: string[][]; attested: false };
};
export type WorkforceOverview = { workforce: Workforce | null; modes: Mode[]; default_mode: Mode };

export type OptionProvider = {
  id: number; name: string; kind: string; enabled: boolean; is_mock: boolean; is_bridge: boolean;
  models: { name: string; display_name?: string | null }[] | string[];
  terms_acknowledged?: boolean | null; health_fresh?: boolean | null; usable?: boolean | null;
  sessions?: { id: string; title: string | null; model: string | null; account: string | null; account_hash: string | null; state: string }[];
  pooled_models?: string[];
};
export type Options = { providers: OptionProvider[]; people: { id: number; username: string }[] };

export type Progress = { total: number; by_lifecycle: Partial<Record<Lifecycle, number>>; completed: number;
  settled: number; with_people: number; under_review: number };
export type Objective = {
  id: number; title: string; prompt: string; status: "planning" | "running" | "completed" | "failed" | "cancelled";
  error: string | null; review_policy: "manager" | "human"; project_id: number; created_by_user_id: number | null;
  created_at: string; updated_at: string; progress: Progress;
  manager: { provider: string; model: string; is_mock: boolean } | null; plan_summary: string | null;
  prompt_redacted: boolean;
  report: { id: number; version: number; status: string; is_mock: boolean; counts: Record<ReportClass, number>; created_at: string } | null;
};
export type Analysis = { understanding: string; deliverables: string[]; assumptions: string[]; risks: string[];
  open_questions: string[]; complexity: string };
export type ObjectiveFull = Objective & {
  analysis: Analysis | null; waiting: Record<string, string>; tasks: TaskCard[];
  dependencies: { task_id: number; depends_on_id: number }[];
};

export type Can = { accept: boolean; decline: boolean; submit: boolean; review: boolean; reassign: boolean; reopen: boolean; cancel: boolean };
export type TaskCard = {
  id: number; objective_id: number; title: string; lifecycle: Lifecycle; status: string; priority: number;
  mode: Mode | null; assignment_source: string | null;
  worker: { id: number; label: string; slot: number; name: string | null; mode: Mode; assignee_user_id: number | null } | null;
  attempts: number; max_attempts: number; required_skills: string[]; criteria: number;
  critical_operation: string | null; kind?: "text" | "code"; needs_human_review: boolean; review_reason: string | null;
  waiting: { reason: string; since: string } | null;
  fallback: { reason: string; note?: string | null } | null;
  accepted_by: { id: number; username: string } | null; approval_id: number | null; error: string | null;
  created_at: string; updated_at: string; started_at: string | null; finished_at: string | null; can?: Can;
};
export type Attachment = { id: string; filename: string; size: number; sha256: string; content_type: string };
export type Review = {
  outcome?: string; reason?: string; advisory?: string; needs_human?: boolean; unmet?: number[]; overridden?: string;
  flagged_injection?: boolean; flagged_secret?: boolean; policy?: string; revisions_used?: number; max_revisions?: number;
  verdict?: { decision: string; feedback: string; summary: string;
    criteria: { index: number; met: boolean; evidence: string }[] } | null;
  evidence_verified?: Record<string, boolean>;
  manager?: { provider: string; model: string; is_mock: boolean; step_id: string } | null;
  human?: { by: string; note: string; self_reviewed?: boolean } | null;
};
export type Submission = {
  id: number; task_id: number; version: number; attempt: number; mode: Mode; source: string; submitted_by: string;
  execution_id: string | null; worker_id: number | null; content: string; sha256: string; notes: string;
  attachments: Attachment[]; is_mock: boolean; review: Review | null;
  provider: { kind?: string; model?: string; request_id?: string | null; usage_reported?: boolean;
    account_binding?: Binding | null } | null;
  decision: "approved" | "revision_requested" | "rejected" | null; decided_by: string | null;
  decided_at: string | null; created_at: string;
};
export type Transition = { id: number; from: Lifecycle | null; to: Lifecycle; actor: string; reason: string;
  details: Record<string, unknown>; at: string };
export type Failure = { at: string; code: string; category: string; action: "retry" | "human" | "fail"; attempt: number;
  delivered: boolean | null; message: string; execution_id: string | null };
export type TaskDetail = TaskCard & {
  can: Can; objective: { id: number; title: string; status: string; review_policy: string };
  task_objective: string | null; instructions: string | null; expected_output: string | null;
  acceptance_criteria: string[]; timeout_seconds: number | null;
  depends_on: { id: number; title: string; lifecycle: Lifecycle }[];
  dependents: { id: number; title: string; lifecycle: Lifecycle }[];
  review_notes: string[]; failures: Failure[]; declines: { by: string; reason: string; at: string }[];
  reassignments: { by: string; from?: string | null; to?: string | null; note: string; at: string }[];
  transitions: Transition[]; submissions: Submission[]; pending_attachments: Attachment[];
  limits: { content_chars: number; notes_chars: number; attachments: number; attachment_bytes: number; extensions: string[] };
  brief?: { message: string; sha256: string; chars: number };
  development?: DevInfo;
};
export type DevCheck = {
  ok: boolean; code: string; feedback: string; needs_human?: boolean;
  files?: { path: string; action: "write" | "delete"; bytes: number | null }[];
  worktree_id?: number; branch?: string; base_branch?: string; base_commit?: string; head_commit?: string | null;
  tests?: { ran: boolean; passed?: boolean; command?: string[]; exit_code?: number; timed_out?: boolean;
    duration_s?: number; output?: string; error?: string } | null;
};
export type DevInfo = {
  files_shown: string[];
  worktree: { id: number; branch: string; base_branch: string; status: string; head_commit: string | null;
    modified_files: string[]; conflicts: Record<string, unknown>[]; merged_at: string | null } | null;
  last_check: DevCheck | null; last_check_version: number | null;
  merged: { merged?: string; into?: string; merge_commit?: string } | null;
};
export type ExecEvent = { seq: number; ts: string; type: string; payload: Record<string, unknown> };
export type TaskList = { total: number; tasks: TaskCard[]; lifecycles: Lifecycle[] };

export type ReportItem = {
  task_id: number; title: string; lifecycle: Lifecycle; class: ReportClass; reason: string | null;
  basis: Record<string, unknown>; worker: string | null; worker_name: string | null; mode: Mode | null;
  attempts: number; versions: number; critical_operation: string | null;
  submission: { id: number; version: number; sha256: string; source: string; submitted_by: string;
    decided_by: string | null; decided_at: string | null; chars: number; attachments: number;
    provider_request_id: string | null; account_binding: string | null } | null;
  is_mock: boolean; mock_reasons: string[]; excerpt: string | null;
};
export type Report = {
  id: number; workflow_id: number; version: number; status: "complete" | "partial" | "failed"; is_mock: boolean;
  created_at: string;
  content: {
    title: string; objective: string; status: string; objective_status: string;
    planned_by: { provider: string; model: string; is_mock: boolean } | null;
    counts: Record<ReportClass, number>; tasks: number; completed: number; facts: string[]; items: ReportItem[];
    narrative: { summary: string; next_steps: string[]; written_by: { provider: string; model: string; is_mock: boolean } } | null;
    narrative_error: string | null; is_mock: boolean; classes: ReportClass[]; generated_at: string;
  };
};

export type ManagerStep = {
  id: string; kind: "analyze" | "plan" | "review" | "consolidate"; status: "ok" | "invalid" | "failed"; attempt: number;
  task_id: number | null; submission_id: number | null; provider: string | null; model: string | null;
  is_mock: boolean; started_at: string; ended_at: string | null; duration_ms: number | null; error: string | null;
  trace_id: string | null; provider_request_id: string | null; usage_reported: boolean;
  usage: { input_tokens?: number; output_tokens?: number } | null;
};
export type HistoryExecution = {
  id: string; status: string; engine: string; provider: string | null; model: string | null; is_mock: boolean;
  attempts: number; retries: { seq: number; at: string; attempt: number; after_seconds: number }[];
  idempotency_key: string | null; provider_request_id: string | null; provider_request_ids: string[];
  trace_ids: string[]; created_at: string; started_at: string | null; ended_at: string | null;
  error_category: string | null; error: string | null; recovered_from: string | null;
  provider_errors: { seq: number; at: string; code: string | null; category: string | null; delivered: boolean | null;
    retry_after: number | null; request_id: string | null }[];
  usage_reported: boolean; usage: { input_tokens: number | null; output_tokens: number | null; cost_usd: string | null } | null;
};
export type History = {
  objective_id: number; thread_id: string; status: string; created_at: string; updated_at: string;
  manager_steps: ManagerStep[];
  tasks: {
    id: number; title: string; lifecycle: Lifecycle; attempts: number; mode: Mode | null;
    worker: { id: number; label: string; agent_id: number; name: string | null } | null;
    started_at: string | null; finished_at: string | null; transitions: Transition[]; executions: HistoryExecution[];
    submissions: { id: number; version: number; attempt: number; mode: Mode; source: string; submitted_by: string;
      execution_id: string | null; sha256: string; chars: number; is_mock: boolean; created_at: string;
      decision: string | null; decided_by: string | null; outcome: string | null; needs_human: boolean;
      manager_step_id: string | null; provider_request_id: string | null }[];
    approvals: { id: number; operation: string; status: string; approver_id: number | null; decided_at: string | null }[];
    failures: Failure[];
  }[];
  reports: { id: number; version: number; status: string; is_mock: boolean; created_at: string }[];
  usage_note: string;
};

export type DashboardBridge = {
  id: number; name: string; enabled: boolean; status: string; is_mock: boolean; terms_acknowledged: boolean;
  reachable: boolean | null; ready: boolean | null; usable: boolean; checked_at: string | null; health_fresh: boolean;
  accounts: number; queue: { depth: number; active: number; max_depth: number } | null;
};
export type Dashboard = {
  workforce: Workforce | null; bridges: DashboardBridge[];
  objectives: { by_status: Record<string, number>; latest: Objective[] };
  tasks: { by_lifecycle: Partial<Record<Lifecycle, number>>; open: number };
  queues: { offered_to_me: number; accepted_by_me: number; needs_human_review: number; pending_approvals: number };
  recent: { id: number; action: string; actor: string; level: string; task_id: number | null;
    details: Record<string, unknown>; at: string }[];
};

export type BridgeSettings = {
  host_header: string; effective_host_header: string | null; stream: boolean; connect_timeout_s: number;
  idle_timeout_s: number; request_timeout_s: number; replay_window_s: number; read_session_registry: boolean;
  max_prompt_chars: number; max_concurrent: number;
};
/** A bridge error as the API reports it (BridgeError.as_dict); older readings may hold a plain string. */
export type BridgeErrorInfo = { code?: string | null; message?: string | null; status?: number | null; category?: string | null };
export type BridgeHealth = {
  checked_at: string; reachable: boolean; ok?: boolean; ready?: boolean; key_ok?: boolean | null; service?: string | null;
  version?: string | null; mock?: boolean; error?: BridgeErrorInfo | string | null; sessions_error?: BridgeErrorInfo | null;
  queue?: { depth: number; active: number; max_depth: number } | null;
  accounts?: { account: string; hash: string; priority: number | null; disabled: boolean; error: string | null }[];
  sessions?: { session_id: string; model: string | null; title: string | null; account: string | null;
    account_hash: string | null; state: string }[];
  sessions_read?: boolean;
};
/** One line of text for a bridge error, whatever shape it arrived in. */
export function bridgeErrorText(e: BridgeErrorInfo | string | null | undefined): string {
  if (!e) return "";
  if (typeof e === "string") return e;
  const head = [e.code, e.status ? `HTTP ${e.status}` : null].filter(Boolean).join(" · ");
  return [head, e.message].filter(Boolean).join(" — ");
}
export type Bridge = {
  id: number; name: string; kind: string; base_url: string; enabled: boolean; is_mock: boolean; status: string;
  last_tested_at: string | null; last_error: string | null; has_key: boolean; key_hint: string | null;
  settings: BridgeSettings;
  terms: { acknowledged: boolean; by: string | null; at: string | null; terms_url: string; statement: string };
  health: BridgeHealth | null; health_fresh: boolean; agent_ids: number[];
};
export type BridgeList = { default_url: string; in_container: boolean; bridges: Bridge[] };
export type DiagStep = { step: string; ok: boolean | null; detail: string; hint: string | null; code?: string };
export type Diagnosis = { ok: boolean; ready: boolean; mock: boolean; steps: DiagStep[]; checked_at: string; bridge: Bridge };

// ---------- queries ----------
const send = <T,>(path: string, body?: unknown, method = "POST") =>
  api<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });
const W = "/api/workforce";

export const useWorkforce = () =>
  useQuery({ queryKey: ["wf"], queryFn: () => api<WorkforceOverview>(W), refetchInterval: 15_000 });
export const useWfOptions = (enabled: boolean) =>
  useQuery({ queryKey: ["wf-options"], queryFn: () => api<Options>(`${W}/options`), enabled });
export const useWfDashboard = () =>
  useQuery({ queryKey: ["wf-dashboard"], queryFn: () => api<Dashboard>(`${W}/dashboard`), refetchInterval: 8_000 });
export const useObjectives = () =>
  useQuery({ queryKey: ["wf-objectives"], queryFn: () => api<Objective[]>(`${W}/objectives`), refetchInterval: 10_000 });
const ACTIVE = new Set(["planning", "running"]);
export const useObjective = (id?: number) =>
  useQuery({ queryKey: ["wf-objective", id], queryFn: () => api<ObjectiveFull>(`${W}/objectives/${id}`), enabled: !!id,
    refetchInterval: (q) => (q.state.data && !ACTIVE.has(q.state.data.status) ? 20_000 : 3_000) });
export const useReport = (id: number | undefined, enabled: boolean, version?: number) =>
  useQuery({ queryKey: ["wf-report", id, version ?? "latest"], enabled: !!id && enabled, retry: false,
    queryFn: () => api<Report>(`${W}/objectives/${id}/report${version ? `?version=${version}` : ""}`) });
export const useHistory = (id: number | undefined, enabled: boolean) =>
  useQuery({ queryKey: ["wf-history", id], enabled: !!id && enabled,
    queryFn: () => api<History>(`${W}/objectives/${id}/history`) });

export type TaskFilters = { queue?: string; objective?: number; worker?: number; mode?: string; lifecycle?: string; q?: string };
export const useWfTasks = (f: TaskFilters) =>
  useQuery({
    queryKey: ["wf-tasks", f], refetchInterval: 6_000,
    queryFn: () => {
      const sp = new URLSearchParams();
      if (f.queue) sp.set("queue", f.queue);
      if (f.objective) sp.set("objective_id", String(f.objective));
      if (f.worker) sp.set("worker_id", String(f.worker));
      if (f.mode) sp.set("mode", f.mode);
      if (f.lifecycle) sp.append("lifecycle", f.lifecycle);
      if (f.q) sp.set("q", f.q);
      return api<TaskList>(`${W}/tasks?${sp}`);
    },
  });
export const useWfTask = (id?: number) =>
  useQuery({ queryKey: ["wf-task", id], queryFn: () => api<TaskDetail>(`${W}/tasks/${id}`), enabled: !!id, refetchInterval: 5_000 });
export const useExecEvents = (id: string | null | undefined, after: number) =>
  useQuery({ queryKey: ["exec-events", id, after], enabled: !!id, refetchInterval: 2_000,
    queryFn: () => api<ExecEvent[]>(`/api/executions/${id}/events?after=${after}&limit=500`) });
export const useBridges = () =>
  useQuery({ queryKey: ["wf-bridges"], queryFn: () => api<BridgeList>(`${W}/bridges`), refetchInterval: 15_000 });

// ---------- mutations ----------
const KEYS = ["wf", "wf-options", "wf-dashboard", "wf-objectives", "wf-objective", "wf-report", "wf-history",
  "wf-tasks", "wf-task", "wf-bridges", "approvals"];

export function useWorkforceActions() {
  const qc = useQueryClient();
  const done = () => KEYS.forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const m = <A, R>(fn: (a: A) => Promise<R>) => useMutation({ mutationFn: fn, onSuccess: done });
  return {
    setup: m((_: void) => send<WorkforceOverview>(`${W}/setup`)),
    updateManager: m((patch: Record<string, unknown>) => send<WorkforceOverview>(`${W}/manager`, patch, "PATCH")),
    updateDevelopment: m((patch: { enabled?: boolean; test_command?: string[] | null; timeout_seconds?: number }) =>
      send<Workforce>(`${W}/development`, patch, "PATCH")),
    updateWorker: m(({ id, patch }: { id: number; patch: Record<string, unknown> }) =>
      send<Worker>(`${W}/workers/${id}`, patch, "PATCH")),
    openManagerBrowser: m(({ site }: { site?: string }) => send<WorkforceOverview>(`${W}/manager/browser`, { site: site ?? "arena" })),
    addWorker: m((b: { name: string; role: string; skills?: string[]; focus?: string }) => send<Worker>(`${W}/workers`, b)),
    archiveWorker: m((id: number) => send<void>(`${W}/workers/${id}`, undefined, "DELETE")),
    openWorkerBrowser: m(({ id, site }: { id: number; site?: string }) =>
      send<Worker>(`${W}/workers/${id}/browser`, { site: site ?? "arena" })),
    submitObjective: m((b: { prompt: string; title?: string; review_policy?: "manager" | "human" }) =>
      send<Objective>(`${W}/objectives`, b)),
    cancelObjective: m(({ id, reason }: { id: number; reason: string }) => send(`${W}/objectives/${id}/cancel`, { reason })),
    tick: m((id: number) => send(`${W}/objectives/${id}/tick`)),
    accept: m((id: number) => send<TaskCard>(`${W}/tasks/${id}/accept`)),
    decline: m(({ id, reason }: { id: number; reason: string }) => send<TaskCard>(`${W}/tasks/${id}/decline`, { reason })),
    submit: m(({ id, content, notes, attachment_ids }: { id: number; content: string; notes: string; attachment_ids: string[] }) =>
      send<Submission>(`${W}/tasks/${id}/submit`, { content, notes, attachment_ids })),
    upload: m(({ id, file }: { id: number; file: File }) =>
      api<Attachment>(`${W}/tasks/${id}/attachments?filename=${encodeURIComponent(file.name)}`, {
        method: "POST", body: file, headers: { "Content-Type": file.type || "application/octet-stream" } })),
    removeAttachment: m((id: string) => send(`${W}/attachments/${id}`, undefined, "DELETE")),
    review: m(({ id, decision, note }: { id: number; decision: "approve" | "revise" | "reject"; note: string }) =>
      send<TaskCard>(`${W}/tasks/${id}/review`, { decision, note })),
    reviewRetry: m((id: number) => send(`${W}/tasks/${id}/review/retry`)),
    reassign: m(({ id, worker_id, note }: { id: number; worker_id: number; note: string }) =>
      send<TaskCard>(`${W}/tasks/${id}/reassign`, { worker_id, note })),
    reopen: m(({ id, note }: { id: number; note: string }) => send<TaskCard>(`${W}/tasks/${id}/reopen`, { note })),
    cancelTask: m(({ id, note }: { id: number; note: string }) => send<TaskCard>(`${W}/tasks/${id}/cancel`, { note })),
    // Arena Local Bridge instances
    createBridge: m((b: { name: string; base_url: string; api_key: string; settings?: Record<string, unknown> }) =>
      send<Bridge>(`${W}/bridges`, b)),
    updateBridge: m(({ id, patch }: { id: number; patch: Record<string, unknown> }) =>
      send<Bridge>(`${W}/bridges/${id}`, patch, "PATCH")),
    setBridgeKey: m(({ id, api_key }: { id: number; api_key: string }) => send<Bridge>(`${W}/bridges/${id}/key`, { api_key }, "PUT")),
    removeBridge: m((id: number) => send(`${W}/bridges/${id}`, undefined, "DELETE")),
    acknowledge: m(({ id, statement }: { id: number; statement: string }) => send<Bridge>(`${W}/bridges/${id}/terms`, { statement })),
    revoke: m((id: number) => send<Bridge>(`${W}/bridges/${id}/terms`, undefined, "DELETE")),
    testBridge: m((id: number) => send<Diagnosis>(`${W}/bridges/${id}/test`)),
    refreshHealth: m((id: number) => send<Bridge>(`${W}/bridges/${id}/health`)),
    discover: m((id: number) => send<{ discovered: number; added: number; mock: boolean }>(`${W}/bridges/${id}/discover`)),
  };
}
