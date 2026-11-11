"use client";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

/** Which cached views each server topic invalidates. Messages carry only a topic and ids (never content). */
const TOPICS: Record<string, string[]> = {
  execution: ["executions", "execution", "exec-events", "runtime", "tasks", "task", "agents", "agent"],
  runtime: ["executions", "execution", "runtime", "agents", "agent"],
  orchestration: ["tasks", "task", "workflows", "workflow", "workflows-all"],
  tasks: ["tasks", "task", "workflows", "workflow", "workflows-all"],
  evaluation: ["tasks", "task", "eval-recent"],
  approvals: ["approvals", "tasks", "task", "budgets"],
  agents: ["agents", "agent"],
  supervisor: ["sv-health", "sv-findings", "sv-actions"],
  devplane: ["worktrees", "pulls", "repos"],
  memory: ["memories", "memory", "context"],
  budget: ["budgets", "usage"],
  automation: ["automations", "automation-runs", "workflows-all"],
  browser: ["bridge", "bridge-sessions", "bridge-session", "bridge-status"],
  workforce: ["wf", "wf-dashboard", "wf-objectives", "wf-objective", "wf-tasks", "wf-task", "wf-bridges", "wf-report", "wf-history", "wf-options"],
  teams: ["teams"],
  projects: ["projects", "analysis"],
};
export type LiveState = "connecting" | "live" | "offline";

function csrf(): string {
  return document.cookie.split("; ").find((c) => c.startsWith("acc_csrf="))?.slice("acc_csrf=".length) ?? "";
}

/** UX-02: one WebSocket per tab; reconnects with backoff; polling stays as the fallback when offline. */
export function useLiveUpdates(): LiveState {
  const qc = useQueryClient();
  const [state, setState] = useState<LiveState>("connecting");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    let ws: WebSocket | null = null;
    let attempt = 0;
    let closed = false;
    const pending = new Set<string>();
    const flush = () => {
      timer.current = null;
      for (const k of pending) qc.invalidateQueries({ queryKey: [k] });
      window.dispatchEvent(new CustomEvent("acc:live", { detail: [...pending] }));
      pending.clear();
    };
    const connect = () => {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      ws = new WebSocket(`${proto}//${location.host}/api/ws`);
      ws.onopen = () => ws?.send(JSON.stringify({ csrf: csrf() }));
      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data) as { topic: string };
          if (msg.topic === "hello") { attempt = 0; setState("live"); return; }
          (TOPICS[msg.topic] ?? []).forEach((k) => pending.add(k));
          pending.add("dashboard");
          if (!timer.current) timer.current = setTimeout(flush, 300); // coalesce bursts
        } catch { /* ignore malformed */ }
      };
      ws.onclose = () => {
        if (closed) return;
        setState("offline");
        attempt += 1;
        setTimeout(connect, Math.min(30_000, 1_000 * 2 ** Math.min(attempt, 5)));
      };
    };
    connect();
    return () => { closed = true; ws?.close(); if (timer.current) clearTimeout(timer.current); };
  }, [qc]);
  return state;
}
