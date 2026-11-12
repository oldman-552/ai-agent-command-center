"use client";
import { useSyncExternalStore } from "react";

// ADR-036: the simplified navigation. The AI Workforce is the way of working; the older general-purpose pages
// (agents, teams, orchestration, executions, …) still exist and still power it, but are hidden from the menu until
// the owner turns on "Show advanced tools" in Settings. A per-browser convenience only: no page is removed and every
// address still opens; the backend enforces permissions either way.
const KEY = "acc-nav-advanced";
const listeners = new Set<() => void>();

function read(): boolean {
  try { return window.localStorage.getItem(KEY) === "1"; } catch { return false; }
}

export function setShowAdvanced(on: boolean) {
  try { window.localStorage.setItem(KEY, on ? "1" : "0"); } catch { /* storage blocked: stays simplified */ }
  listeners.forEach((l) => l());
}

function subscribe(l: () => void) {
  listeners.add(l);
  const onStorage = (e: StorageEvent) => { if (e.key === KEY) l(); };
  window.addEventListener("storage", onStorage);
  return () => { listeners.delete(l); window.removeEventListener("storage", onStorage); };
}

export function useShowAdvanced(): boolean {
  return useSyncExternalStore(subscribe, read, () => false);
}

/** Where a person lands after signing in: the Operations Room in the simplified layout, the dashboard otherwise. */
export function homePath(): string {
  return typeof window !== "undefined" && read() ? "/" : "/workforce/room";
}
