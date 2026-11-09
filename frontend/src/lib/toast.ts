import { create } from "zustand";

export type Toast = { id: number; kind: "success" | "error"; text: string };
type S = { toasts: Toast[]; push: (kind: Toast["kind"], text: string) => void; dismiss: (id: number) => void };

let seq = 0;
export const useToasts = create<S>((set) => ({
  toasts: [],
  push: (kind, text) => {
    const id = ++seq;
    set((s) => ({ toasts: [...s.toasts, { id, kind, text }] }));
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), 5000);
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
}));
