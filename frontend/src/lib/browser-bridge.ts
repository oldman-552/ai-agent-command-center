"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type BridgeProvider = {
  key: string; name: string; url: string; terms_url: string; verified: boolean; requires_terms: boolean; models: string[];
  notes: string; experimental: true; unlocked: boolean; acknowledgement: { by: string; at: string } | null; statement: string | null;
  selectors: Record<string, string | null>; default_selectors: Record<string, string | null>; overridden: boolean; custom: boolean;
};
export type BridgeProfile = { name: string; label: string; owner: string | null; created_at: string;
  active_session: { id: number; provider: string; state: string } | null };
export type SiteIn = { key: string; name: string; url: string; terms_url?: string; input?: string; send?: string; response?: string };
export type BridgeOverview = {
  enabled: boolean; experimental: true; providers: BridgeProvider[];
  health: { reachable: boolean; error?: string; live_sessions?: number; max_sessions?: number };
};
export type BridgeSession = {
  id: number; provider: string; profile: string; state: string; error: string | null; agent_id: number | null; model: string | null;
  turns: number; last_activity_at: string | null; created_at: string;
  history?: { role: "user" | "assistant"; text: string; at: string; cancelled?: boolean }[];
};
export type BridgeStatus = { id: number; state: string; error: string | null; live: boolean; partial: string | null; turns: number };

const LIVE = new Set(["STARTING", "SENDING", "GENERATING"]);
const getOverview = () => api<BridgeOverview>("/api/browser-bridge");
const getSessions = () => api<BridgeSession[]>("/api/browser-bridge/sessions");
const getSession = (id: number) => api<BridgeSession>(`/api/browser-bridge/sessions/${id}`);
const getProfiles = () => api<BridgeProfile[]>("/api/browser-bridge/profiles");
export const useBridgeProfiles = () => useQuery({ queryKey: ["bridge-profiles"], queryFn: getProfiles, refetchInterval: 5_000 });
const getStatus = (id: number) => api<BridgeStatus>(`/api/browser-bridge/sessions/${id}/status`);
const send = <T,>(path: string, body?: unknown, method = "POST") => api<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });

export const useBridge = () => useQuery({ queryKey: ["bridge"], queryFn: getOverview });
export const useBridgeSessions = () => useQuery({ queryKey: ["bridge-sessions"], queryFn: getSessions, refetchInterval: 5_000 });
export const useBridgeSession = (id?: number) =>
  useQuery({ queryKey: ["bridge-session", id], queryFn: () => getSession(id!), enabled: !!id, refetchInterval: (q) => (q.state.data && LIVE.has(q.state.data.state) ? 1_000 : false) });
export const useBridgeStatus = (id: number | undefined, live: boolean) =>
  useQuery({ queryKey: ["bridge-status", id], queryFn: () => getStatus(id!), enabled: !!id && live, refetchInterval: live ? 700 : false });

export function useBridgeActions() {
  const qc = useQueryClient();
  const done = () => ["bridge", "bridge-sessions", "bridge-session", "bridge-status", "bridge-profiles"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const m = <A, R>(fn: (a: A) => Promise<R>) => useMutation({ mutationFn: fn, onSuccess: done });
  return {
    setEnabled: m((enabled: boolean) => send<BridgeOverview>("/api/browser-bridge/enabled", { enabled }, "PUT")),
    acknowledge: m(({ provider, statement }: { provider: string; statement: string }) => send(`/api/browser-bridge/providers/${provider}/acknowledge`, { statement })),
    revoke: m((provider: string) => send(`/api/browser-bridge/providers/${provider}/acknowledge`, undefined, "DELETE")),
    selectors: m(({ provider, selectors }: { provider: string; selectors: Record<string, string> }) =>
      send(`/api/browser-bridge/providers/${provider}/selectors`, { selectors }, "PUT")),
    addSite: m((b: SiteIn) => send("/api/browser-bridge/sites", b)),
    removeSite: m((key: string) => send(`/api/browser-bridge/sites/${key}`, undefined, "DELETE")),
    addProfile: m((b: { name: string; label: string; owner: string | null }) => send<BridgeProfile[]>("/api/browser-bridge/profiles", b)),
    removeProfile: m((name: string) => send<BridgeProfile[]>(`/api/browser-bridge/profiles/${name}`, undefined, "DELETE")),
    create: m((b: { provider: string; profile?: string }) => send<BridgeStatus>("/api/browser-bridge/sessions", b)),
    op: m(({ id, op }: { id: number; op: "start" | "stop" | "recheck" | "cancel" | "conversation" }) => send<BridgeStatus>(`/api/browser-bridge/sessions/${id}/${op}`)),
    prompt: m(({ id, prompt }: { id: number; prompt: string }) => send<BridgeStatus>(`/api/browser-bridge/sessions/${id}/prompt`, { prompt })),
  };
}
