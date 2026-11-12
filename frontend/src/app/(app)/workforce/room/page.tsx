"use client";
/* The Operations Room: the AI Workforce as a live map — you, the Manager, the queue, the workers, review, the main
   branch — over a background that reacts to what the workforce is doing. Every number and state here comes from
   /api/workforce (dashboard, objective, tasks, history) and the Approval Center; effects fire only on real changes
   between two readings, never on a timer. */
import { useQuery } from "@tanstack/react-query";
import { Bot, Maximize2, Pause, Play, Plus, Sparkles, User, Volume2, VolumeX, X } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useCan } from "@/components/providers";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { MockBadge, ModeBadge, PageHeader, useActor, useFail, WorkforceTabs } from "@/components/workforce/shared";
import { api } from "@/lib/api";
import { type Approval, useApprovals, useDecide } from "@/lib/approvals";
import { useToasts } from "@/lib/toast";
import {
  type Dashboard, type History, type Lifecycle, type TaskCard, type Worker, useExecEvents, useObjective, useWfDashboard,
  useWfTask, useWfTasks, useWorkforceActions,
} from "@/lib/workforce";
import s from "./room.module.css";

type View = "map" | "sum" | "tv";
type WState = "work" | "person" | "offer" | "ask" | "review" | "revise" | "block" | "idle" | "off";
const SLOT_NAMES = ["--w1", "--w2", "--w3", "--w4", "--w5", "--w6", "--w7", "--w8"];  // up to 20 workers cycle these (ADR-036)
const SLOT_VARS = SLOT_NAMES.map((n) => `var(${n})`);
const slotColor = (slot: number) => SLOT_VARS[(slot - 1) % SLOT_VARS.length];
const STATE_COLOR: Record<WState, string> = {
  work: "var(--ok)", person: "var(--ok)", offer: "var(--ask)", ask: "var(--ask)", review: "var(--merge)",
  revise: "var(--ask)", block: "var(--bad)", idle: "var(--muted-foreground)", off: "var(--muted-foreground)",
};
const STEP_OF: Partial<Record<Lifecycle, number>> = {
  PLANNED: 0, READY: 0, ASSIGNED: 0, WAITING_FOR_ACCEPTANCE: 0, REVISION_REQUESTED: 1, ACCEPTED: 1, QUEUED: 1, RUNNING: 1,
  SUBMITTED: 2, UNDER_REVIEW: 2, APPROVED: 3, COMPLETED: 4,
};
const STEPS = ["assigned", "working", "delivered", "reviewed", "done"] as const;
const WORKING: Lifecycle[] = ["ASSIGNED", "QUEUED", "RUNNING"];
const SETTLED: Lifecycle[] = ["COMPLETED", "FAILED", "REJECTED", "CANCELLED"];
const PRIORITY: Lifecycle[] = ["RUNNING", "QUEUED", "ASSIGNED", "ACCEPTED", "WAITING_FOR_ACCEPTANCE", "UNDER_REVIEW",
  "SUBMITTED", "APPROVED", "REVISION_REQUESTED", "READY", "BLOCKED", "PLANNED", "FAILED", "REJECTED"];
const initial = (w: Worker) => (w.name.split("—").pop() || w.name).trim().charAt(0) || w.label;

function workerView(w: Worker, tasks: TaskCard[]): { state: WState; task: TaskCard | null } {
  const mine = tasks.filter((t) => t.worker?.id === w.id && t.lifecycle !== "COMPLETED" && t.lifecycle !== "CANCELLED");
  const pick = mine.find((t) => t.needs_human_review) ??
    PRIORITY.map((l) => mine.find((t) => t.lifecycle === l)).find(Boolean) ?? null;
  if (w.mode === "DISABLED" || w.status.state === "disabled") return { state: "off", task: pick };
  if (!pick) return { state: w.status.state === "unavailable" ? "off" : "idle", task: null };
  const l = pick.lifecycle;
  const state: WState = pick.needs_human_review ? "ask" : WORKING.includes(l) ? "work" : l === "ACCEPTED" ? "person"
    : l === "WAITING_FOR_ACCEPTANCE" ? "offer" : ["SUBMITTED", "UNDER_REVIEW", "APPROVED"].includes(l) ? "review"
      : l === "REVISION_REQUESTED" ? "revise" : l === "FAILED" || l === "REJECTED" || l === "BLOCKED" ? "block" : "idle";
  return { state, task: pick };
}

// ---------------- sound (off until the person turns it on) ----------------
let audio: AudioContext | null = null;
function tone(on: boolean, notes: number[], type: OscillatorType = "sine", dur = .16, gap = .09, vol = .05) {
  if (!on || typeof window === "undefined") return;
  try {
    audio = audio ?? new AudioContext();
    const now = audio.currentTime;
    notes.forEach((f, i) => {
      const o = audio!.createOscillator(), g = audio!.createGain(), t0 = now + i * gap;
      o.type = type; o.frequency.value = f;
      g.gain.setValueAtTime(0, t0); g.gain.linearRampToValueAtTime(vol, t0 + .01); g.gain.exponentialRampToValueAtTime(.0001, t0 + dur);
      o.connect(g); g.connect(audio!.destination); o.start(t0); o.stop(t0 + dur + .02);
    });
  } catch { /* audio is optional */ }
}

function stored<T>(key: string, fallback: T): T {
  try { const v = localStorage.getItem(key); return v === null ? fallback : (JSON.parse(v) as T); } catch { return fallback; }
}
function store(key: string, v: unknown) { try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* optional */ } }

// ---------------- living background + effects ----------------
type Wave = { x: number; y: number; r: number; c: string; p: number };
type Spark = { x: number; y: number; vx: number; vy: number; life: number; c: string; r: number; rot: number; vr: number };
type Streak = { x: number; y: number; vx: number; vy: number; c: string; len: number };
type Packet = { path: SVGPathElement; el: SVGCircleElement; t: number; len: number; dur: number; done?: () => void };

function hexA(h: string, a: number) {
  let x = (h || "#888888").trim().replace("#", "");
  if (x.length === 3) x = x.split("").map((c) => c + c).join("");
  const n = parseInt(x.slice(0, 6), 16) || 0;
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`;
}

type DivRef = React.RefObject<HTMLDivElement | null>;
type CanvasRef = React.RefObject<HTMLCanvasElement | null>;
function useEffects(room: DivRef, bg: CanvasRef, fx: CanvasRef, live: { working: string[]; asking: boolean; on: boolean }) {
  const st = useRef({ waves: [] as Wave[], sparks: [] as Spark[], streaks: [] as Streak[], packets: [] as Packet[],
    flash: 0, aurora: 0, mouse: { x: -999, y: -999 }, frame: 0, live });
  st.current.live = live;
  const reduce = useRef(false);
  useEffect(() => {
    reduce.current = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const S = st.current;
    let raf = 0, last = performance.now();
    const colors = () => {
      const cs = room.current ? getComputedStyle(room.current) : null;
      const v = (n: string, d: string) => (cs?.getPropertyValue(n).trim() || d);
      return { dot: v("--dot", "#8a97b8"), primary: v("--primary", "#7c3aed"), ask: v("--ask", "#b45309"), bad: v("--bad", "#dc2626") };
    };
    const move = (e: PointerEvent) => {
      const r = bg.current?.getBoundingClientRect();
      if (r) { S.mouse.x = e.clientX - r.left; S.mouse.y = e.clientY - r.top; }
    };
    const leave = () => { S.mouse.x = S.mouse.y = -999; };
    const el = room.current;
    el?.addEventListener("pointermove", move);
    el?.addEventListener("pointerleave", leave);
    const loop = (now: number) => {
      const dt = now - last; last = now; S.frame++;
      // packets on the map
      S.packets = S.packets.filter((k) => {
        k.t += dt / k.dur;
        const pt = k.path.getPointAtLength(Math.min(1, k.t) * k.len);
        k.el.setAttribute("cx", String(pt.x)); k.el.setAttribute("cy", String(pt.y));
        if (k.t >= 1) { k.el.remove(); k.done?.(); return false; }
        return true;
      });
      const c = bg.current, f = fx.current;
      if (c && !reduce.current) {
        const dpr = Math.min(2, devicePixelRatio || 1), W = c.clientWidth, H = c.clientHeight;
        if (c.width !== Math.round(W * dpr) || c.height !== Math.round(H * dpr)) { c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); }
        const g = c.getContext("2d")!;
        g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
        if (S.live.on) {
          const col = colors(), e = S.live.working.length, GAP = W < 600 ? 28 : 34;
          const glow = .05 + e * .025 + S.aurora * .22;
          [[.2, .25, col.primary, 0], [.8, .7, col.primary, 2]].forEach(([x, y, cc, ph], i) => {
            const bx = ((x as number) + Math.sin(S.frame * .004 + (ph as number)) * .1) * W;
            const by = ((y as number) + Math.cos(S.frame * .0035 + (ph as number)) * .08) * H, r = .55 * Math.max(W, H);
            const tint = S.aurora > 0 && S.live.working.length ? S.live.working[(i + (S.frame >> 5)) % S.live.working.length] : cc as string;
            const gr = g.createRadialGradient(bx, by, 0, bx, by, r);
            gr.addColorStop(0, hexA(tint, glow)); gr.addColorStop(1, hexA(tint, 0)); g.fillStyle = gr; g.fillRect(0, 0, W, H);
          });
          if (S.live.asking) {
            const k = (Math.sin(S.frame * .08) + 1) / 2, gr = g.createRadialGradient(W / 2, H + 80, 0, W / 2, H + 80, H * .8);
            gr.addColorStop(0, hexA(col.ask, .05 + k * .1)); gr.addColorStop(1, hexA(col.ask, 0)); g.fillStyle = gr; g.fillRect(0, 0, W, H);
          }
          for (let x = GAP / 2; x < W; x += GAP) for (let y = GAP / 2; y < H; y += GAP) {
            let a = .22, r = 1.2, cc = col.dot;
            const dm = Math.hypot(x - S.mouse.x, y - S.mouse.y);
            if (dm < 150) { const k = 1 - dm / 150; a += k * .6; r += k * 1.6; cc = col.primary; }
            for (const w of S.waves) {
              const d = Math.abs(Math.hypot(x - w.x, y - w.y) - w.r);
              if (d < 24) { const k = (1 - d / 24) * w.p * Math.max(0, 1 - w.r / Math.max(W, H)); a += k; r += k * 2; cc = w.c; }
            }
            g.fillStyle = hexA(cc, Math.min(1, a)); g.beginPath(); g.arc(x, y, r, 0, 6.283); g.fill();
          }
          if (e && Math.random() < e * .035) {
            const cc = S.live.working[Math.floor(Math.random() * e)], hz = Math.random() < .5, dir = Math.random() < .5 ? 1 : -1, v = 2 + e;
            S.streaks.push(hz ? { x: dir > 0 ? -60 : W + 60, y: Math.round(Math.random() * H / GAP) * GAP + GAP / 2, vx: dir * v, vy: 0, c: cc, len: 60 + Math.random() * 60 }
              : { x: Math.round(Math.random() * W / GAP) * GAP + GAP / 2, y: dir > 0 ? -60 : H + 60, vx: 0, vy: dir * v, c: cc, len: 60 + Math.random() * 60 });
          }
          S.streaks = S.streaks.filter((k) => k.x > -200 && k.x < W + 200 && k.y > -200 && k.y < H + 200);
          g.lineCap = "round";
          S.streaks.forEach((k) => {
            k.x += k.vx; k.y += k.vy;
            const sp = Math.hypot(k.vx, k.vy), tx = k.x - k.vx / sp * k.len, ty = k.y - k.vy / sp * k.len;
            const gr = g.createLinearGradient(tx, ty, k.x, k.y); gr.addColorStop(0, hexA(k.c, 0)); gr.addColorStop(1, hexA(k.c, .8));
            g.strokeStyle = gr; g.lineWidth = 2; g.beginPath(); g.moveTo(tx, ty); g.lineTo(k.x, k.y); g.stroke();
          });
          S.waves = S.waves.filter((w) => Math.max(W, H) * 1.1 - w.r > 0);
          S.waves.forEach((w) => {
            w.r += 7 + w.p * 4; g.strokeStyle = hexA(w.c, .45 * w.p * Math.max(0, 1 - w.r / Math.max(W, H)));
            g.lineWidth = 3; g.beginPath(); g.arc(w.x, w.y, w.r, 0, 6.283); g.stroke();
          });
          if (S.flash > 0) { g.fillStyle = hexA(col.bad, S.flash * .14 * (S.frame % 6 < 3 ? 1 : .4)); g.fillRect(0, 0, W, H); S.flash -= .03; }
          if (S.aurora > 0) S.aurora -= .003;
        }
      }
      if (f) {
        const dpr = Math.min(2, devicePixelRatio || 1), W = innerWidth, H = innerHeight;
        if (f.width !== Math.round(W * dpr)) { f.width = Math.round(W * dpr); f.height = Math.round(H * dpr); }
        const g = f.getContext("2d")!;
        g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
        S.sparks = S.sparks.filter((p) => p.life-- > 0);
        S.sparks.forEach((p) => {
          p.vy += .2; p.vx *= .985; p.x += p.vx; p.y += p.vy; p.rot += p.vr;
          g.save(); g.translate(p.x, p.y); g.rotate(p.rot); g.globalAlpha = Math.min(1, p.life / 30); g.fillStyle = p.c;
          g.fillRect(-p.r / 2, -p.r / 4, p.r, p.r / 2); g.restore();
        });
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => { cancelAnimationFrame(raf); el?.removeEventListener("pointermove", move); el?.removeEventListener("pointerleave", leave); };
  }, [room, bg, fx]);

  const local = useCallback((sx: number, sy: number) => {
    const r = bg.current?.getBoundingClientRect();
    return r ? [sx - r.left, sy - r.top] : [sx, sy];
  }, [bg]);
  return useMemo(() => ({
    wave(screenX: number, screenY: number, c: string, p = 1) {
      if (reduce.current) return;
      const [x, y] = local(screenX, screenY); st.current.waves.push({ x, y, r: 0, c, p });
    },
    burst(x: number, y: number, n: number, cols: string[]) {
      if (reduce.current) return;
      for (let i = 0; i < n; i++) {
        const an = Math.random() * 6.283, sp = 2 + Math.random() * 6;
        st.current.sparks.push({ x, y, vx: Math.cos(an) * sp, vy: Math.sin(an) * sp - 3, life: 50 + Math.random() * 45,
          c: cols[i % cols.length], r: 2.5 + Math.random() * 2.5, rot: Math.random() * 6, vr: Math.random() * .6 - .3 });
      }
    },
    flash() { if (!reduce.current) st.current.flash = 1; },
    aurora() { if (!reduce.current) st.current.aurora = 1; },
    packet(path: SVGPathElement | null | undefined, color: string, layer: SVGGElement | null, done?: () => void, size = 6) {
      if (reduce.current || !path || !layer || !path.getAttribute("d")) { done?.(); return; }
      const el = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      el.setAttribute("r", String(size)); el.style.fill = color; el.style.filter = `drop-shadow(0 0 6px ${color})`;
      layer.appendChild(el);
      st.current.packets.push({ path, el, t: 0, len: path.getTotalLength(), dur: 1000, done });
    },
  }), [local]);
}

const center = (el: Element | null | undefined): [number, number] => {
  const r = el?.getBoundingClientRect();
  return r ? [r.left + r.width / 2, r.top + r.height / 2] : [innerWidth / 2, innerHeight / 2];
};

// ================================================================== page
export default function OperationsRoomPage() {
  const t = useTranslations("workforce.room");
  const tw = useTranslations("workforce");
  const can = useCan();
  const toast = useToasts((x) => x.push);
  const dash = useWfDashboard();
  const d = dash.data;
  const force = d?.workforce ?? null;
  const active = d?.objectives.latest.find((o) => o.status === "planning" || o.status === "running") ?? d?.objectives.latest[0];
  const obj = useObjective(active?.id);
  const tasks = useMemo(() => obj.data?.tasks ?? [], [obj.data]);
  const [view, setView] = useState<View>("map");
  const [sound, setSound] = useState(false);
  const [bgOn, setBgOn] = useState(true);
  const [askToasts, setAskToasts] = useState(true);
  const [watch, setWatch] = useState<number | null>(null);
  const [goalOpen, setGoalOpen] = useState(false);
  const [big, setBig] = useState(false);
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    setView(stored<View>("acc-room-view", "map")); setSound(stored("acc-room-sound", false));
    setBgOn(stored("acc-room-bg", true)); setAskToasts(stored("acc-room-ask", true));
  }, []);
  const workers = useMemo(() => force?.workers ?? [], [force]);
  const views = useMemo(() => workers.map((w) => ({ w, ...workerView(w, tasks) })), [workers, tasks]);
  const working = views.filter((v) => v.state === "work" || v.state === "person");
  const asking = views.some((v) => v.state === "ask" || v.state === "offer");

  const room = useRef<HTMLDivElement>(null), bg = useRef<HTMLCanvasElement>(null), fx = useRef<HTMLCanvasElement>(null);
  const [wcolors, setWcolors] = useState<string[]>([]);
  useEffect(() => {
    const read = () => {
      const cs = room.current ? getComputedStyle(room.current) : null;
      setWcolors(SLOT_NAMES.map((n) => cs?.getPropertyValue(n).trim() || "#5b8cff"));
    };
    read();
    const mo = new MutationObserver(read);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => mo.disconnect();
  }, []);
  const effects = useEffects(room, bg, fx, {
    working: paused ? [] : working.map((v) => wcolors[(v.w.slot - 1) % SLOT_NAMES.length] || "#5b8cff"), asking: asking && !paused, on: bgOn,
  });

  // ---- map geometry: edges between the nodes, recomputed on resize ----
  const mapRef = useRef<HTMLDivElement>(null);
  const edgeRefs = useRef<Record<string, SVGPathElement | null>>({});
  const pktLayer = useRef<SVGGElement>(null);
  const nodeRefs = useRef<Record<string, HTMLElement | null>>({});
  const [, setGeo] = useState(0);
  const layout = useCallback(() => {
    const m = mapRef.current?.getBoundingClientRect();
    if (!m) return;
    const box = (k: string) => {
      const el = nodeRefs.current[k];
      const inner = el?.querySelector("[data-anchor]") ?? el;
      return inner?.getBoundingClientRect();
    };
    const pt = (k: string, side: "l" | "r" | "t" | "b"): [number, number] | null => {
      const b = box(k);
      if (!b || !b.width) return null;
      const x = b.left - m.left + b.width / 2, y = b.top - m.top + b.height / 2;
      return side === "l" ? [b.left - m.left, y] : side === "r" ? [b.right - m.left, y] : side === "t" ? [x, b.top - m.top] : [x, b.bottom - m.top];
    };
    const rtl = document.documentElement.dir !== "ltr";
    const [from, to] = rtl ? ["l", "r"] as const : ["r", "l"] as const;
    const L = (a: [number, number] | null, b: [number, number] | null) => (a && b ? `M${a[0]} ${a[1]}L${b[0]} ${b[1]}` : "");
    const C = (a: [number, number] | null, b: [number, number] | null) => {
      if (!a || !b) return ""; const my = (a[1] + b[1]) / 2;
      return `M${a[0]} ${a[1]}C${a[0]} ${my} ${b[0]} ${my} ${b[0]} ${b[1]}`;
    };
    const set = (k: string, dd: string) => edgeRefs.current[k]?.setAttribute("d", dd);
    set("mp", L(pt("mgr", from), pt("plan", to)));
    set("pq", L(pt("plan", from), pt("queue", to)));
    for (const w of workers) {
      set(`q-${w.id}`, C(pt("queue", "b"), pt(`w${w.id}`, "t")));
      set(`${w.id}-r`, C(pt(`w${w.id}-tk`, "b"), pt("review", "t")));
    }
    set("rm", L(pt("review", from), pt("main", to)));
    setGeo((x) => x + 1);
  }, [workers]);
  useEffect(() => {
    if (!mapRef.current) return;
    const ro = new ResizeObserver(layout);
    ro.observe(mapRef.current);
    layout();
    return () => ro.disconnect();
  }, [layout, view, tasks.length]);

  // ---- effects from real changes between two readings ----
  const prev = useRef<{ life: Record<number, Lifecycle>; review: Set<number>; obj?: string; id?: number } | null>(null);
  useEffect(() => {
    if (!obj.data) return;
    const life: Record<number, Lifecycle> = {}, review = new Set<number>();
    tasks.forEach((x) => { life[x.id] = x.lifecycle; if (x.needs_human_review) review.add(x.id); });
    const p = prev.current;
    prev.current = { life, review, obj: obj.data.status, id: obj.data.id };
    if (!p || p.id !== obj.data.id || paused) return;
    for (const x of tasks) {
      const was = p.life[x.id], now = x.lifecycle, wid = x.worker?.id, col = wid ? wcolors[((x.worker?.slot ?? 1) - 1) % SLOT_NAMES.length] : "#5b8cff";
      if (was === now) continue;
      if (wid && ["QUEUED", "RUNNING", "ACCEPTED"].includes(now) && !["QUEUED", "RUNNING", "ACCEPTED"].includes(was ?? "")) {
        effects.packet(edgeRefs.current[`q-${wid}`], col, pktLayer.current);
        tone(sound, [440], "sine", .06, 0, .02);
      }
      if (wid && now === "SUBMITTED") effects.packet(edgeRefs.current[`${wid}-r`], col, pktLayer.current);
      if (now === "COMPLETED") {
        effects.packet(edgeRefs.current.rm, "#b49bff", pktLayer.current, () => {
          const [cx, cy] = center(nodeRefs.current.main);
          effects.burst(cx, cy, 60, [...wcolors, "#ffffff", "#b49bff"]); effects.wave(cx, cy, "#b49bff", 1.2);
        }, 7);
        tone(sound, [523, 659, 784, 1047], "triangle", .22, .08, .05);
      }
      if ((now === "FAILED" || now === "REJECTED") && was !== now) {
        effects.flash(); tone(sound, [196, 147], "sawtooth", .18, .12, .025);
        const [cx, cy] = center(wid ? nodeRefs.current[`w${wid}`] : null); effects.wave(cx, cy, "#ff6380", 1);
      }
    }
    const fresh = tasks.filter((x) => x.needs_human_review && !p.review.has(x.id));
    if (fresh.length) {
      tone(sound, [880, 660], "sine", .2, .14, .045);
      fresh.forEach((x) => {
        const [cx, cy] = center(x.worker ? nodeRefs.current[`w${x.worker.id}`] : null); effects.wave(cx, cy, "#ffb547", .9);
        if (askToasts) toast("success", t("toast.needsYou", { title: x.title }));
      });
    }
    if (p.obj !== "completed" && obj.data.status === "completed") {
      effects.aurora(); tone(sound, [523, 659, 784, 1047, 1319, 1568], "triangle", .35, .1, .05);
      for (let i = 0; i < 5; i++) setTimeout(() => effects.burst(innerWidth * (.15 + Math.random() * .7), innerHeight * (.15 + Math.random() * .45), 70, [...wcolors, "#b49bff", "#ffffff"]), i * 260);
      toast("success", t("toast.objectiveDone"));
    }
  }, [obj.data, tasks, effects, sound, wcolors, askToasts, toast, t, paused]);

  const demo = () => {
    const [cx, cy] = center(nodeRefs.current.review);
    effects.wave(cx, cy, "#ffb547", 1); tone(sound, [880, 660], "sine", .2, .14, .045);
    setTimeout(() => { const [x, y] = center(nodeRefs.current.main); effects.burst(x, y, 70, [...wcolors, "#fff", "#b49bff"]); effects.wave(x, y, "#b49bff", 1.2); tone(sound, [523, 659, 784, 1047], "triangle", .22, .08, .05); }, 1400);
    setTimeout(() => { effects.flash(); tone(sound, [196, 147], "sawtooth", .18, .12, .025); }, 2800);
    setTimeout(() => { effects.aurora(); for (let i = 0; i < 4; i++) setTimeout(() => effects.burst(innerWidth * (.2 + Math.random() * .6), innerHeight * (.2 + Math.random() * .4), 60, [...wcolors, "#fff"]), i * 250); }, 4200);
    toast("success", t("demoNote"));
  };

  const setV = (v: View) => { setView(v); store("acc-room-view", v); };
  const pending = d?.queues ?? { offered_to_me: 0, accepted_by_me: 0, needs_human_review: 0, pending_approvals: 0 };
  const completed = tasks.filter((x) => x.lifecycle === "COMPLETED").length;
  const isMock = !!(force?.manager.is_mock || active?.manager?.is_mock);

  if (dash.isLoading) return <div className="mx-auto max-w-6xl"><PageHeader title={t("title")} /><p className="text-muted-foreground" aria-busy="true">…</p></div>;
  if (!force) {
    return (
      <div className="mx-auto flex max-w-6xl flex-col gap-6">
        <PageHeader title={t("title")} subtitle={t("subtitle")} />
        <WorkforceTabs />
        <p className="text-muted-foreground">{tw("notSetUpHelp")} <Link className="text-primary underline" href="/workforce">{tw("tabs.overview")}</Link></p>
      </div>
    );
  }

  const top = (
    <div className={s.top}>
      <div>
        <h1 className={s.title}>{t("title")}</h1>
        <div className={s.sub}>
          {active ? <span>{t("goal", { title: active.title })}</span> : <span>{t("noGoal")}</span>}
          {active && <span>· {t("progress", { done: completed, total: tasks.length })}</span>}
          {isMock && <MockBadge short />}
        </div>
      </div>
      <div role="tablist" aria-label={t("views")} className={`${s.glass} ${s.tabs}`}>
        {(["map", "sum", "tv"] as View[]).map((v) => (
          <button key={v} role="tab" aria-selected={view === v} className={s.tab} onClick={() => setV(v)}>{t(`view.${v}`)}</button>
        ))}
      </div>
      <div className={s.tools}>
        <div className={`${s.glass} ${s.pill}`} title={t("energyHelp")}>{t("energy")}
          <span className={s.energy} role="img" aria-label={t("workingNow", { n: working.length })}>
            {workers.map((w, i) => <i key={w.id} data-on={i < working.length} />)}
          </span>
        </div>
        <div className={`${s.glass} ${s.pill}`}>{t("needsYouShort")} <b data-testid="room-needs">{pending.needs_human_review + pending.offered_to_me + pending.pending_approvals}</b></div>
        <button className={`${s.glass} ${s.iconBtn}`} aria-pressed={paused} onClick={() => setPaused(!paused)} aria-label={paused ? t("resumeFx") : t("pauseFx")}>
          {paused ? <Play aria-hidden /> : <Pause aria-hidden />}
        </button>
        <button className={`${s.glass} ${s.iconBtn}`} aria-pressed={sound} aria-label={t("sound")}
          onClick={() => { const v = !sound; setSound(v); store("acc-room-sound", v); tone(v, [660, 880], "sine", .12, .08, .04); }}>
          {sound ? <Volume2 aria-hidden /> : <VolumeX aria-hidden />}
        </button>
        <button className={`${s.glass} ${s.iconBtn}`} onClick={demo}><Sparkles aria-hidden />{t("demo")}</button>
        {can("workforce.run") && <Button onClick={() => setGoalOpen(true)} data-testid="room-new-goal"><Plus aria-hidden />{t("newGoal")}</Button>}
      </div>
    </div>
  );

  return (
    <div className="mx-auto flex max-w-[1560px] flex-col gap-4">
      <WorkforceTabs />
      <div ref={room} className={`${s.room} ${big ? s.fullscreen : ""}`} data-testid="ops-room">
        <canvas ref={bg} className={s.bg} aria-hidden />
        <div className={s.inner}>
          {top}
          {!force.manager.configured && (
            <div className={`${s.glass} ${s.rc}`} role="status">{t("managerMissing")} <Link className="text-primary underline" href="/workforce/workers">{tw("tabs.workers")}</Link></div>
          )}
          {view === "map" && (
            <div className={s.grid}>
              <div className={s.col}>
                <MapView workers={views} mapRef={mapRef} edgeRefs={edgeRefs} pktLayer={pktLayer} nodeRefs={nodeRefs}
                  objective={obj.data ?? null} tasks={tasks} onWatch={setWatch} completed={completed} force={force} />
                {active && <Timeline objectiveId={active.id} workers={workers} />}
              </div>
              <Rail d={d!} tasks={tasks} views={views} effects={effects} wcolors={wcolors} sound={sound} />
            </div>
          )}
          {view === "sum" && <Summary d={d!} sound={sound} setSound={(v) => { setSound(v); store("acc-room-sound", v); }}
            bgOn={bgOn} setBgOn={(v) => { setBgOn(v); store("acc-room-bg", v); }}
            askToasts={askToasts} setAskToasts={(v) => { setAskToasts(v); store("acc-room-ask", v); }} />}
          {view === "tv" && <BigScreen d={d!} views={views} tasks={tasks} objectiveTitle={active?.title ?? null}
            big={big} setBig={setBig} />}
        </div>
      </div>
      <canvas ref={fx} className={s.fx} aria-hidden />
      {watch !== null && <WatchDrawer worker={workers.find((w) => w.id === watch)!} task={views.find((v) => v.w.id === watch)?.task ?? null}
        onClose={() => setWatch(null)} />}
      <GoalDialog open={goalOpen} onOpenChange={setGoalOpen} ready={force.manager.configured} />
    </div>
  );
}

// ================================================================== map
function MapView({ workers, mapRef, edgeRefs, pktLayer, nodeRefs, objective, tasks, onWatch, completed, force }: {
  workers: { w: Worker; state: WState; task: TaskCard | null }[]; mapRef: React.RefObject<HTMLDivElement | null>;
  edgeRefs: React.RefObject<Record<string, SVGPathElement | null>>; pktLayer: React.RefObject<SVGGElement | null>;
  nodeRefs: React.RefObject<Record<string, HTMLElement | null>>; objective: { status: string; title: string } | null;
  tasks: TaskCard[]; onWatch: (id: number) => void; completed: number; force: NonNullable<Dashboard["workforce"]>;
}) {
  const t = useTranslations("workforce.room");
  const tl = useTranslations("workforce.lifecycle");
  const a = useWorkforceActions();
  const fail = useFail();
  // Seats: one row for up to five workers; more (up to 20) wrap into rows of at most five, smaller, and the map grows
  // one band per extra row (ADR-036).
  const n = workers.length || 1;
  const rows = Math.ceil(n / 5);
  const perRow = Math.ceil(n / rows);
  const compact = n > 5;  // smaller seats once there is more than one row
  const seat = (i: number): React.CSSProperties => {
    const r = Math.floor(i / perRow), inRow = Math.min(perRow, n - r * perRow), c = i - r * perRow;
    const x = 100 - (100 / inRow) * (c + .5);  // right to left in Arabic; mirrored by `dir`
    // one row sits in the middle of the map; several rows get a fixed 210px band each below the top hubs
    return { insetInlineStart: `${100 - x}%`, top: rows === 1 ? "52%" : `${150 + 105 + r * 210}px` };
  };
  const waiting = tasks.filter((x) => ["PLANNED", "READY", "BLOCKED"].includes(x.lifecycle));
  const inReview = tasks.filter((x) => ["SUBMITTED", "UNDER_REVIEW", "APPROVED"].includes(x.lifecycle)).length;
  const planning = objective?.status === "planning";
  const anyWork = workers.some((v) => v.state === "work" || v.state === "person");
  const reg = (k: string) => (el: HTMLElement | null) => { nodeRefs.current[k] = el; };
  const edge = (k: string, color: string, on: boolean) => (
    <path key={k} ref={(el) => { edgeRefs.current[k] = el; }} className={s.edge} data-on={on} style={{ stroke: color, color }} />
  );
  // with rows of seats the map grows, so the hubs keep a fixed distance from its top and bottom edge
  const pos = (x: number, y: number) => ({ insetInlineStart: `${100 - x}%`,
    top: !compact ? `${y}%` : y < 50 ? "70px" : "calc(100% - 65px)" } as React.CSSProperties);
  return (
    <section className={`${s.glass} ${s.mapCard}`} aria-labelledby="room-map-h">
      <div className={s.map} ref={mapRef} style={{ "--rows": rows, "--per": perRow } as React.CSSProperties} data-compact={compact || undefined}>
        <svg className={s.edges} aria-hidden>
          <g>
            {edge("mp", "var(--primary)", planning)}
            {edge("pq", "var(--primary)", waiting.length > 0 || planning)}
            {workers.map((v) => edge(`q-${v.w.id}`, slotColor(v.w.slot), v.state === "work" || v.state === "person"))}
            {workers.map((v) => edge(`${v.w.id}-r`, slotColor(v.w.slot), v.state === "review"))}
            {edge("rm", "var(--merge)", inReview > 0)}
          </g>
          <g ref={pktLayer} />
        </svg>
        <div className={s.maphead}>
          <div className="flex items-center gap-2"><h2 id="room-map-h" className={s.h2}>{t("map")}</h2><span className={s.live}><i />{t("live")}</span></div>
          <div className={s.legend}>
            <span><i className={s.dot} style={{ background: "var(--ok)" }} />{t("legend.work")}</span>
            <span><i className={s.dot} style={{ background: "var(--ask)" }} />{t("legend.you")}</span>
            <span><i className={s.dot} style={{ background: "var(--merge)" }} />{t("legend.review")}</span>
            <span><i className={s.dot} style={{ background: "var(--bad)" }} />{t("legend.stopped")}</span>
            <span>{t("legend.click")}</span>
          </div>
        </div>
        <div className={s.slot} style={pos(89, 15)} ref={reg("mgr")}>
          <div className={s.hub} data-anchor><div className={s.ic} style={{ background: "color-mix(in srgb,var(--primary) 18%,transparent)", color: "var(--primary)" }}><User aria-hidden className="size-4" /></div>
            <div><b>{t("you")}</b><small>{objective ? objective.title : t("noGoal")}</small></div></div>
        </div>
        <div className={s.slot} style={pos(64, 15)} ref={reg("plan")}>
          <div className={s.hub} data-anchor><div className={s.ic} style={{ background: "color-mix(in srgb,var(--merge) 18%,transparent)", color: "var(--merge)" }}><Bot aria-hidden className="size-4" /></div>
            <div><b>{t("manager")}</b><small>{!force.manager.configured ? t("managerOff") : planning ? t("planning") : anyWork ? t("watching") : t("waitingGoal")}</small></div></div>
        </div>
        <div className={s.slot} style={pos(39, 15)} ref={reg("queue")}>
          <div className={s.hub} data-anchor><div className={s.ic} style={{ background: "color-mix(in srgb,var(--ask) 18%,transparent)", color: "var(--ask)" }}>{waiting.length}</div>
            <div><b>{t("queue")}</b><small>{waiting[0] ? t("next", { title: waiting[0].title }) : t("queueEmpty")}</small></div></div>
        </div>
        {workers.map((v, i) => {
          const step = v.task ? STEP_OF[v.task.lifecycle] ?? 0 : -1;
          const frac = step < 0 ? 0 : (step + 1) / STEPS.length;
          const sc = STATE_COLOR[v.state];
          return (
            <div key={v.w.id} className={s.slot} style={seat(i)} ref={reg(`w${v.w.id}`)}>
              <button className={s.node} data-state={v.state} data-testid={`room-worker-${v.w.slot}`}
                style={{ "--c": slotColor(v.w.slot), "--sc": sc } as React.CSSProperties}
                onClick={() => onWatch(v.w.id)}>
                <span className={s.av} data-anchor>
                  <svg width="88" height="88" viewBox="0 0 88 88" aria-hidden><circle cx="44" cy="44" r="39" stroke="var(--border)" strokeWidth="5" fill="none" />
                    <circle className={s.ring} cx="44" cy="44" r="39" stroke={slotColor(v.w.slot)} strokeWidth="5" fill="none" strokeDasharray="245"
                      strokeDashoffset={(245 * (1 - frac)).toFixed(1)} strokeLinecap="round" /></svg>
                  <span className={s.in}>{initial(v.w)}</span>
                  <span className={s.step}>{step < 0 ? "—" : t(`step.${STEPS[step]}`)}</span>
                </span>
                <span className={s.nm}>{v.w.label} · {(v.w.name.split("—").pop() || v.w.name).trim()}</span>
                <span className={s.st}><i />{t(`state.${v.state}`)}</span>
                <span className={s.tk} ref={reg(`w${v.w.id}-tk`)}>{v.task ? v.task.title : t("noTask")}</span>
                <span className="sr-only">{t("legend.click")}</span>
              </button>
              {v.state === "ask" && v.task && (
                <div className={s.bubble} role="group" aria-label={t("reviewThis")}>
                  <p>{t("bubbleReview", { title: v.task.title })}</p>
                  <div className={s.opts}>
                    <button className={`${s.opt} ${s.optMerge}`} disabled={a.review.isPending}
                      onClick={() => a.review.mutate({ id: v.task!.id, decision: "approve", note: "" }, { onError: fail })}>{t("approve")}</button>
                    <Link className={`${s.opt} ${s.optGhost}`} href={`/workforce/tasks?task=${v.task.id}`}>{t("open")}</Link>
                  </div>
                </div>
              )}
              {v.state === "offer" && v.task && (
                <div className={s.bubble}>
                  <p>{t("bubbleOffer")}</p>
                  <div className={s.opts}><Link className={s.opt} href={`/workforce/tasks?task=${v.task.id}`}>{t("takeIt")}</Link></div>
                </div>
              )}
              <span className="sr-only">{v.task ? tl(v.task.lifecycle) : ""}</span>
            </div>
          );
        })}
        <div className={s.slot} style={pos(56, 91)} ref={reg("review")}>
          <div className={s.hub} data-anchor><div className={s.ic} style={{ background: "color-mix(in srgb,var(--merge) 18%,transparent)", color: "var(--merge)" }}>{inReview}</div>
            <div><b>{t("review")}</b><small>{t("reviewHelp")}</small></div></div>
        </div>
        <div className={s.slot} style={pos(28, 91)} ref={reg("main")}>
          <div className={s.hub} data-anchor><div className={s.ic} style={{ background: "color-mix(in srgb,var(--ok) 18%,transparent)", color: "var(--ok)" }}>{completed}</div>
            <div><b>{t("done")}</b><small>{t("doneHelp")}</small></div></div>
        </div>
      </div>
    </section>
  );
}

// ================================================================== timeline
function Timeline({ objectiveId, workers }: { objectiveId: number; workers: Worker[] }) {
  const t = useTranslations("workforce.room");
  const fmt = useFormatter();
  const h = useQuery({ queryKey: ["wf-history", objectiveId], refetchInterval: 10_000,
    queryFn: () => api<History>(`/api/workforce/objectives/${objectiveId}/history`) });
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const i = setInterval(() => setNow(Date.now()), 30_000); return () => clearInterval(i); }, []);
  const lanes = useMemo(() => {
    const out: Record<number, { segs: [number, number, boolean][]; marks: [number, string, string][] }> = {};
    workers.forEach((w) => { out[w.id] = { segs: [], marks: [] }; });
    for (const task of h.data?.tasks ?? []) {
      const wid = task.worker?.id;
      if (!wid || !out[wid]) continue;
      let open: number | null = null;
      for (const tr of [...task.transitions].sort((x, y) => +new Date(x.at) - +new Date(y.at))) {
        const at = +new Date(tr.at);
        if (["ACCEPTED", "QUEUED", "RUNNING"].includes(tr.to) && open === null) open = at;
        if (["SUBMITTED", "FAILED", "CANCELLED", "REJECTED"].includes(tr.to) && open !== null) { out[wid].segs.push([open, at, false]); open = null; }
        if (tr.to === "COMPLETED") out[wid].marks.push([at, "●", "var(--mergefill)"]);
        if (tr.to === "REVISION_REQUESTED") out[wid].marks.push([at, "↺", "var(--askfill)"]);
        if (tr.to === "FAILED" || tr.to === "REJECTED") out[wid].marks.push([at, "▲", "var(--bad)"]);
        if (tr.to === "WAITING_FOR_ACCEPTANCE") out[wid].marks.push([at, "◆", "var(--askfill)"]);
      }
      if (open !== null) out[wid].segs.push([open, now, true]);
    }
    return out;
  }, [h.data, workers, now]);
  const all = Object.values(lanes).flatMap((l) => [...l.segs.map((x) => x[0]), ...l.marks.map((x) => x[0])]);
  const start = Math.min(now - 3 * 3600_000, ...(all.length ? all : [now]));
  const end = now + 15 * 60_000, span = end - start;
  const P = (ms: number) => `${(((ms - start) / span) * 100).toFixed(2)}%`;
  const ticks: number[] = [];
  const step = span > 10 * 3600_000 ? 3 * 3600_000 : 3600_000;
  for (let x = Math.ceil(start / step) * step; x <= end; x += step) ticks.push(x);
  return (
    <section className={`${s.glass} ${s.tl}`} aria-labelledby="room-tl-h">
      <div className={s.ch}><h2 id="room-tl-h" className={s.h2}>{t("timeline")}</h2><span className={s.hint}>{t("timelineHelp")}</span></div>
      {workers.map((w) => (
        <div key={w.id} className={s.lane}>
          <div className={s.ln}><i className={s.dot} style={{ background: slotColor(w.slot) }} />{w.label} · {(w.name.split("—").pop() || w.name).trim()}</div>
          <div className={s.track}>
            {lanes[w.id]?.segs.map(([a, b, open], i) => (
              <div key={i} className={s.seg} style={{ insetInlineStart: P(a), width: `${(((b - a) / span) * 100).toFixed(2)}%`, background: slotColor(w.slot),
                ...(open ? { backgroundImage: "linear-gradient(90deg,rgba(255,255,255,.35),transparent 60%)" } : {}) }} />
            ))}
            {lanes[w.id]?.marks.map(([at, sym, c], i) => (
              <div key={`m${i}`} className={s.mk} style={{ insetInlineStart: P(at), background: c }} title={fmt.dateTime(new Date(at), { hour: "2-digit", minute: "2-digit" })}>{sym}</div>
            ))}
            <div className={s.now} style={{ insetInlineStart: P(now) }} />
          </div>
        </div>
      ))}
      <div className={s.axis}><span /><div className={s.ticks}>
        {ticks.map((x) => <span key={x} style={{ insetInlineStart: P(x) }}>{fmt.dateTime(new Date(x), { hour: "2-digit", minute: "2-digit" })}</span>)}
      </div></div>
    </section>
  );
}

// ================================================================== rail
function Rail({ d, tasks, views, effects, wcolors, sound }: {
  d: Dashboard; tasks: TaskCard[]; views: { w: Worker; state: WState; task: TaskCard | null }[];
  effects: ReturnType<typeof useEffects>; wcolors: string[]; sound: boolean;
}) {
  const t = useTranslations("workforce.room");
  const tw = useTranslations("workforce");
  const fmt = useFormatter();
  const actor = useActor();
  const can = useCan();
  const a = useWorkforceActions();
  const decide = useDecide();
  const fail = useFail();
  const approvals = useApprovals({ status: "pending" }, can("approvals.read"));
  const review = tasks.filter((x) => x.needs_human_review);
  const offered = tasks.filter((x) => x.lifecycle === "WAITING_FOR_ACCEPTANCE");
  const aps = (approvals.data?.items ?? []).filter((x: Approval) => tasks.some((tk) => tk.id === x.task_id) || x.operation === "git.merge_protected").slice(0, 6);
  const total = review.length + offered.length + aps.length;
  const celebrate = (el: HTMLElement) => {
    const [x, y] = center(el); effects.burst(x, y, 60, [...wcolors, "#ffffff", "#b49bff"]); effects.wave(x, y, "#b49bff", 1.2);
    tone(sound, [523, 659, 784, 1047], "triangle", .22, .08, .05);
  };
  const star = useMemo(() => {
    const today = new Date().toDateString();
    const done = new Map<number, number>();
    tasks.forEach((x) => { if (x.lifecycle === "COMPLETED" && x.worker && x.finished_at && new Date(x.finished_at).toDateString() === today) done.set(x.worker.id, (done.get(x.worker.id) ?? 0) + 1); });
    const best = [...done.entries()].sort((p, q) => q[1] - p[1])[0];
    const v = best ? views.find((x) => x.w.id === best[0]) : null;
    return v ? { w: v.w, n: best![1] } : null;
  }, [tasks, views]);
  return (
    <aside className={s.col}>
      <section className={`${s.glass} ${s.rc}`} aria-labelledby="room-need-h">
        <div className={s.ch}><h2 id="room-need-h" className={s.h2}>{t("needsYou")}</h2><span className={s.badge} data-zero={total === 0}>{total}</span></div>
        <div className={s.need} data-testid="room-needs-list">
          {total === 0 && <div className={s.empty}>{t("nothingForYou")}</div>}
          {review.map((x) => (
            <div key={`r${x.id}`} className={`${s.nc} ${s.ncAsk}`}>
              <b>{x.title}</b><small>{x.worker?.label} · {x.review_reason ? (tw.has(`reviewReason.${x.review_reason}`) ? tw(`reviewReason.${x.review_reason}`) : x.review_reason) : t("decideNow")}</small>
              <div className={s.opts}>
                <button className={`${s.opt} ${s.optMerge}`} disabled={a.review.isPending} onClick={(e) => { const el = e.currentTarget; a.review.mutate({ id: x.id, decision: "approve", note: "" }, { onError: fail, onSuccess: () => celebrate(el) }); }}>{t("approve")}</button>
                <Link className={`${s.opt} ${s.optGhost}`} href={`/workforce/tasks?task=${x.id}`}>{t("open")}</Link>
              </div>
            </div>
          ))}
          {aps.map((x) => (
            <div key={`a${x.id}`} className={`${s.nc} ${s.ncMerge}`}>
              <b>{x.title}</b><small>{x.summary}</small>
              {can("approvals.decide") ? (
                <div className={s.opts}>
                  <button className={`${s.opt} ${s.optMerge}`} disabled={decide.isPending}
                    onClick={(e) => { const el = e.currentTarget; decide.mutate({ id: x.id, decision: "approve", reason: "" }, { onError: fail, onSuccess: () => celebrate(el) }); }}>
                    {x.operation === "git.merge_protected" ? t("approveMerge") : t("approve")}</button>
                  <Link className={`${s.opt} ${s.optGhost}`} href={`/approvals?id=${x.id}`}>{t("open")}</Link>
                </div>
              ) : <Link className="text-primary underline" href="/approvals">{t("open")}</Link>}
            </div>
          ))}
          {offered.map((x) => (
            <div key={`o${x.id}`} className={`${s.nc} ${s.ncInfo}`}>
              <b>{x.title}</b><small>{t("offeredTo", { worker: x.worker?.label ?? "-" })}</small>
              <div className={s.opts}><Link className={s.opt} href={`/workforce/tasks?task=${x.id}`}>{t("takeIt")}</Link></div>
            </div>
          ))}
        </div>
      </section>
      <section className={`${s.glass} ${s.rc}`} aria-labelledby="room-star-h">
        <div className={s.ch}><h2 id="room-star-h" className={s.h2}>{t("star")}</h2><span className={s.hint}>{t("starHelp")}</span></div>
        {star ? (
          <div className={s.star} style={{ "--sc": slotColor(star.w.slot) } as React.CSSProperties}>
            <span className={s.starIn}>{initial(star.w)}</span>
            <div><b>{star.w.label} · {(star.w.name.split("—").pop() || star.w.name).trim()}</b><div className="text-xs text-muted-foreground">{t("starDone", { n: star.n })}</div></div>
          </div>
        ) : <div className={s.empty}>{t("starNone")}</div>}
      </section>
      <section className={`${s.glass} ${s.rc}`} aria-labelledby="room-feed-h">
        <div className={s.ch}><h2 id="room-feed-h" className={s.h2}>{t("feed")}</h2><span className={s.live}><i />{t("live")}</span></div>
        <ul className={s.feed} aria-live="polite" tabIndex={0} aria-labelledby="room-feed-h">
          {d.recent.slice(0, 20).map((r) => {
            const key = `activity.${r.action.replace(/^workforce\./, "")}`;
            const c = r.level === "warning" ? "var(--ask)" : r.level === "error" ? "var(--bad)" : "var(--primary)";
            return (
              <li key={r.id}>
                <time dateTime={r.at}>{fmt.dateTime(new Date(r.at), { hour: "2-digit", minute: "2-digit" })}</time>
                <i className={s.dot} style={{ background: c }} />
                <span>{tw.has(key) ? tw(key) : r.action}{r.task_id ? <> <Link className="text-primary" dir="ltr" href={`/workforce/tasks?task=${r.task_id}`}>#{r.task_id}</Link></> : null} <span className="text-muted-foreground">· {actor(r.actor)}</span></span>
              </li>
            );
          })}
        </ul>
      </section>
      <section className={`${s.glass} ${s.rc}`} aria-labelledby="room-team-h">
        <div className={s.ch}><h2 id="room-team-h" className={s.h2}>{t("team")}</h2><Link className={s.hint} href="/workforce/workers">{t("teamEdit")}</Link></div>
        {views.map((v) => (
          <div key={v.w.id} className={s.emp}>
            <span className={s.empIn} style={{ color: slotColor(v.w.slot) }}>{initial(v.w)}</span>
            <div className="min-w-0 flex-1">
              <b>{v.w.label} · {(v.w.name.split("—").pop() || v.w.name).trim()}</b>
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <ModeBadge mode={v.w.mode} />
                {v.w.mode === "HUMAN_ASSISTED" && v.w.assignee && <span>{t("person", { name: v.w.assignee.username })}</span>}
                {v.w.account_label && <span>{v.w.account_label}</span>}
              </div>
            </div>
          </div>
        ))}
        {d.bridges.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-2 text-xs text-muted-foreground">
            {d.bridges.map((b) => <span key={b.id}><i className={s.dot} style={{ background: b.usable ? "var(--ok)" : "var(--bad)" }} /> {b.name}{b.is_mock ? " (mock)" : ""}</span>)}
          </div>
        )}
      </section>
    </aside>
  );
}

// ================================================================== watch a worker
function WatchDrawer({ worker, task, onClose }: { worker: Worker; task: TaskCard | null; onClose: () => void }) {
  const t = useTranslations("workforce.room");
  const tl = useTranslations("workforce.lifecycle");
  const fmt = useFormatter();
  const detail = useWfTask(task?.id);
  const td = detail.data;
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    close.current?.focus();
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    addEventListener("keydown", k); return () => removeEventListener("keydown", k);
  }, [onClose]);
  const exId = useMemo(() => {
    if (!td || !["QUEUED", "RUNNING", "ASSIGNED"].includes(td.lifecycle)) return null;
    const tr = [...td.transitions].reverse().find((x) => typeof x.details?.execution_id === "string");
    return (tr?.details?.execution_id as string) ?? null;
  }, [td]);
  const events = useExecEvents(exId, 0);
  const stream = (events.data ?? []).filter((e) => e.type === "output_delta").map((e) => String(e.payload.text ?? "")).join("");
  const step = td ? STEP_OF[td.lifecycle] ?? 0 : -1;
  const last = td?.submissions.at(-1);
  const dev = td?.development?.last_check;
  const lines: { c: string; text: string }[] = [];
  if (td) {
    for (const tr of td.transitions.slice(-12)) {
      lines.push({ c: s.tM, text: `${fmt.dateTime(new Date(tr.at), { hour: "2-digit", minute: "2-digit" })}  ${tr.from ?? "·"} → ${tr.to}  (${tr.actor})${tr.reason ? `  ${tr.reason}` : ""}` });
    }
    if (exId) lines.push({ c: s.tC, text: `$ execution ${exId.slice(0, 8)} — ${t("streaming")}` });
    if (stream) stream.split("\n").slice(-40).forEach((l) => lines.push({ c: "", text: l }));
    if (dev) {
      lines.push({ c: s.tA, text: `› ${t("devCheck", { code: dev.code })}` });
      dev.files?.forEach((f) => lines.push({ c: f.action === "delete" ? s.tR : s.tG, text: `${f.action === "delete" ? "-" : "+"} ${f.path}` }));
      if (dev.tests?.ran) {
        lines.push({ c: s.tC, text: `$ ${dev.tests.command?.join(" ")}` });
        (dev.tests.output ?? "").split("\n").slice(-15).forEach((l) => lines.push({ c: dev.tests?.passed ? s.tG : s.tR, text: l }));
      }
    }
    if (!exId && last) {
      lines.push({ c: s.tA, text: `› ${t("resultVersion", { v: last.version, chars: last.content.length })}` });
      last.content.split("\n").slice(0, 30).forEach((l) => lines.push({ c: l.startsWith("```") ? s.tC : "", text: l }));
    }
  }
  return (
    <>
      <div className={s.scrim} onClick={onClose} aria-hidden />
      <aside className={s.drawer} role="dialog" aria-modal="true" aria-labelledby="room-watch-h" style={{ "--c": slotColor(worker.slot) } as React.CSSProperties}>
        <div className={s.dh}>
          <span className={s.dhIn}>{initial(worker)}</span>
          <div className="min-w-0 flex-1">
            <div id="room-watch-h" className="text-lg font-bold">{worker.label} · {(worker.name.split("—").pop() || worker.name).trim()}</div>
            <div className="text-xs text-muted-foreground">{td ? `${td.title} · ${tl(td.lifecycle)}` : t("noTask")}</div>
          </div>
          <ModeBadge mode={worker.mode} />
          <button ref={close} className={`${s.iconBtn}`} style={{ border: "1px solid var(--border)" }} onClick={onClose} aria-label={t("close")}><X aria-hidden /></button>
        </div>
        {td && (
          <div className={s.steps}>
            {STEPS.map((x, i) => <span key={x} data-s={i < step ? "done" : i === step ? "now" : "todo"}>{i < step ? "✓" : i === step ? "●" : "○"} {t(`step.${x}`)}</span>)}
          </div>
        )}
        <div className="flex items-center justify-between"><h3 className={s.h2}>{t("screen")}</h3>{exId && <span className={s.live}><i />{t("live")}</span>}</div>
        <div className={s.term} dir="ltr" aria-live="off">
          {lines.length === 0 && <div className={s.tM}>{t("screenEmpty")}</div>}
          {lines.map((l, i) => <div key={i} className={l.c}>{l.text}</div>)}
          {exId && <span className={s.cursor} />}
        </div>
        {td?.kind === "code" && td.development?.worktree && (
          <div className="text-xs text-muted-foreground" dir="ltr">{td.development.worktree.branch} · {td.development.worktree.head_commit?.slice(0, 12) ?? "—"} · {td.development.worktree.status}</div>
        )}
        <p className="text-xs text-muted-foreground">{worker.mode === "AUTOMATIC" ? t("screenAuto") : t("screenHuman")}</p>
        <div className="flex flex-wrap gap-2">
          {td && <Link className="inline-flex min-h-10 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground" href={`/workforce/tasks?task=${td.id}`}>{t("openCard")}</Link>}
          <Link className="inline-flex min-h-10 items-center rounded-md border px-4 text-sm" href="/workforce/workers">{t("settings")}</Link>
        </div>
      </aside>
    </>
  );
}

// ================================================================== summary
function Summary({ d, sound, setSound, bgOn, setBgOn, askToasts, setAskToasts }: {
  d: Dashboard; sound: boolean; setSound: (v: boolean) => void; bgOn: boolean; setBgOn: (v: boolean) => void;
  askToasts: boolean; setAskToasts: (v: boolean) => void;
}) {
  const t = useTranslations("workforce.room");
  const fmt = useFormatter();
  const all = useWfTasks({});
  const tasks = all.data?.tasks ?? [];
  const workers = d.workforce?.workers ?? [];
  const day = (x: Date) => x.toDateString();
  const today = day(new Date());
  const per = workers.map((w) => {
    const mine = tasks.filter((x) => x.worker?.id === w.id && x.lifecycle === "COMPLETED" && x.finished_at && day(new Date(x.finished_at)) === today);
    const mins = mine.map((x) => x.started_at && x.finished_at ? (+new Date(x.finished_at) - +new Date(x.started_at)) / 60000 : null).filter((x): x is number => x !== null);
    return { w, done: mine.length, rework: mine.filter((x) => x.attempts > 1).length, avg: mins.length ? Math.round(mins.reduce((p, q) => p + q, 0) / mins.length) : null };
  });
  const star = [...per].sort((p, q) => (q.done - q.rework * .7) - (p.done - p.rework * .7))[0];
  const fastest = per.filter((p) => p.avg !== null).sort((p, q) => p.avg! - q.avg!)[0];
  const exact = per.filter((p) => p.done).sort((p, q) => p.rework - q.rework || q.done - p.done)[0];
  const days = Array.from({ length: 7 }, (_, i) => { const x = new Date(); x.setDate(x.getDate() - (6 - i)); return x; });
  const counts = days.map((x) => tasks.filter((tk) => tk.lifecycle === "COMPLETED" && tk.finished_at && day(new Date(tk.finished_at)) === day(x)).length);
  const mx = Math.max(1, ...counts);
  const mxDone = Math.max(1, ...per.map((p) => p.done));
  const sw = (on: boolean, set: (v: boolean) => void, label: string) => (
    <div className={s.tog}><span>{label}</span><button className={s.sw} role="switch" aria-checked={on} aria-label={label} onClick={() => set(!on)} /></div>
  );
  return (
    <div className={s.sum}>
      <div className={s.col}>
        <section className={`${s.glass} ${s.rc}`}>
          <div className="text-sm text-muted-foreground">{t("star")}</div>
          {star && star.done ? (
            <div className={`${s.star} mt-2`} style={{ "--sc": slotColor(star.w.slot) } as React.CSSProperties}>
              <span className={s.starIn}>{initial(star.w)}</span>
              <div><div className="text-xl font-bold">{star.w.label} · {(star.w.name.split("—").pop() || star.w.name).trim()}</div>
                <div className="text-sm text-muted-foreground">{t("starLine", { n: star.done, rework: star.rework, avg: star.avg ?? 0 })}</div></div>
            </div>
          ) : <div className={`${s.empty} mt-2`}>{t("starNone")}</div>}
        </section>
        <section className={`${s.glass} ${s.rc}`}>
          <h2 className={s.h2}>{t("awards")}</h2>
          <ul className="mt-2 flex flex-col gap-2 text-sm">
            <li>{t("awardFast")}: {fastest ? `${fastest.w.label} — ${t("minutes", { n: fastest.avg ?? 0 })}` : "—"}</li>
            <li>{t("awardExact")}: {exact ? `${exact.w.label} — ${t("reworks", { n: exact.rework })}` : "—"}</li>
            <li>{t("awardQueue")}: {d.queues.needs_human_review + d.queues.offered_to_me + d.queues.pending_approvals}</li>
          </ul>
        </section>
        <section className={`${s.glass} ${s.rc}`}>
          <h2 className={s.h2}>{t("alerts")}</h2>
          {sw(sound, setSound, t("soundToggle"))}
          {sw(askToasts, setAskToasts, t("askToggle"))}
          {sw(bgOn, setBgOn, t("bgToggle"))}
        </section>
      </div>
      <div className={s.col}>
        <section className={`${s.glass} ${s.rc}`}>
          <div className={s.ch}><h2 className={s.h2}>{t("week")}</h2><span className={s.hint}>{t("weekHelp")}</span></div>
          <div className={s.bars}>
            {counts.map((c, i) => (
              <div key={i} data-today={i === 6}><b>{c}</b><i style={{ height: `${Math.max(4, (c / mx) * 140)}px` }} />
                {i === 6 ? t("today") : fmt.dateTime(days[i], { weekday: "short" })}</div>
            ))}
          </div>
        </section>
        <section className={`${s.glass} ${s.rc}`}>
          <h2 className={`${s.h2} mb-3`}>{t("perWorker")}</h2>
          <div className="flex flex-col gap-3 text-sm">
            {per.map((p) => (
              <div key={p.w.id}><div className="flex justify-between"><span><i className={s.dot} style={{ background: slotColor(p.w.slot) }} /> {p.w.label} · {(p.w.name.split("—").pop() || p.w.name).trim()}</span><b>{t("tasksN", { n: p.done })}</b></div>
                <div className={s.pbar}><i style={{ width: `${(p.done / mxDone) * 100}%`, background: slotColor(p.w.slot) }} /></div></div>
            ))}
          </div>
        </section>
      </div>
      <div className={s.col}>
        <section className={`${s.glass} ${s.rc}`}>
          <h2 className={`${s.h2} mb-2`}>{t("numbers")}</h2>
          {([["objRunning", (d.objectives.by_status.running ?? 0) + (d.objectives.by_status.planning ?? 0)], ["objDone", d.objectives.by_status.completed ?? 0],
            ["openTasks", d.tasks.open], ["reviewWaiting", d.queues.needs_human_review], ["approvalsWaiting", d.queues.pending_approvals]] as const).map(([k, v]) => (
            <div key={k} className="flex justify-between border-b py-1.5 text-sm"><span className="text-muted-foreground">{t(`num.${k}`)}</span><b>{v}</b></div>
          ))}
        </section>
        <section className={`${s.glass} ${s.rc}`}>
          <h2 className={`${s.h2} mb-2`}>{t("latestGoals")}</h2>
          {d.objectives.latest.slice(0, 6).map((o) => (
            <Link key={o.id} href={`/workforce/objectives/${o.id}`} className="flex justify-between gap-2 py-1 text-sm hover:underline">
              <span className="truncate">{o.title}</span><span className="text-muted-foreground">{o.progress.completed}/{o.progress.total}</span>
            </Link>
          ))}
        </section>
      </div>
    </div>
  );
}

// ================================================================== big screen
function BigScreen({ d, views, tasks, objectiveTitle, big, setBig }: {
  d: Dashboard; views: { w: Worker; state: WState; task: TaskCard | null }[]; tasks: TaskCard[];
  objectiveTitle: string | null; big: boolean; setBig: (v: boolean) => void;
}) {
  const t = useTranslations("workforce.room");
  const tw = useTranslations("workforce");
  const tl = useTranslations("workforce.lifecycle");
  const fmt = useFormatter();
  const [clock, setClock] = useState(() => new Date());
  useEffect(() => { const i = setInterval(() => setClock(new Date()), 15_000); return () => clearInterval(i); }, []);
  const waitingN = d.queues.needs_human_review + d.queues.offered_to_me + d.queues.pending_approvals;
  const done = tasks.filter((x) => x.lifecycle === "COMPLETED").length;
  const inWork = tasks.filter((x) => !SETTLED.includes(x.lifecycle) && !["PLANNED", "READY"].includes(x.lifecycle)).length;
  const toggle = () => {
    const next = !big; setBig(next);
    try { if (next) void document.documentElement.requestFullscreen?.(); else if (document.fullscreenElement) void document.exitFullscreen(); } catch { /* optional */ }
  };
  return (
    <div className={s.tv}>
      <div className={s.top}>
        <h2 className="text-2xl font-bold sm:text-4xl">{t("tvTitle")}</h2>
        <div className="flex items-center gap-3">
          <Button variant="outline" onClick={toggle}><Maximize2 aria-hidden />{big ? t("exitBig") : t("enterBig")}</Button>
          <span className="text-2xl tabular-nums text-muted-foreground sm:text-4xl">{fmt.dateTime(clock, { hour: "2-digit", minute: "2-digit" })}</span>
        </div>
      </div>
      <div className={s.tvk}>
        <div className={s.glass}><span className="text-muted-foreground">{t("tvDone")}</span><b style={{ color: "var(--merge)" }}>{done} / {tasks.length}</b></div>
        <div className={s.glass}><span className="text-muted-foreground">{t("tvWorking")}</span><b style={{ color: "var(--ok)" }}>{views.filter((v) => v.state === "work" || v.state === "person").length}</b><span className="text-muted-foreground">{t("ofWorkers", { n: views.length })}</span></div>
        <div className={s.glass}><span className="text-muted-foreground">{t("tvOpen")}</span><b>{d.tasks.open}</b></div>
        <div className={s.glass} style={waitingN ? { borderColor: "var(--ask)", boxShadow: "0 0 34px color-mix(in srgb,var(--ask) 22%,transparent)" } : undefined}>
          <span style={{ color: "var(--ask)" }}>{t("needsYou")}</span><b style={{ color: "var(--ask)" }}>{waitingN}</b></div>
      </div>
      <div className={s.tva}>
        {views.map((v) => {
          const step = v.task ? STEP_OF[v.task.lifecycle] ?? 0 : -1;
          return (
            <div key={v.w.id} className={s.glass} style={{ "--c": slotColor(v.w.slot), ...(v.state === "ask" ? { borderColor: "var(--ask)" } : {}) } as React.CSSProperties}>
              <h3>{v.w.label} · {(v.w.name.split("—").pop() || v.w.name).trim()}</h3>
              <div className="min-h-[2.6em] text-lg">{v.task ? v.task.title : t("noTask")}</div>
              <div className={s.tbar}><i style={{ width: `${step < 0 ? 0 : ((step + 1) / STEPS.length) * 100}%` }} /></div>
              <div className="text-muted-foreground">{v.task ? tl(v.task.lifecycle) : t(`state.${v.state}`)} · {tw(`mode.${v.w.mode}`)}</div>
            </div>
          );
        })}
      </div>
      {objectiveTitle && (
        <div className={`${s.glass} ${s.rc}`}>
          <div className="mb-3 flex flex-wrap justify-between gap-2"><span className="text-xl font-semibold">{t("goal", { title: objectiveTitle })}</span>
            <span style={{ color: "var(--merge)" }} className="font-bold">{t("progress", { done, total: tasks.length })}</span></div>
          <div className={s.gsegs}>{tasks.map((x, i) => <i key={x.id} data-s={x.lifecycle === "COMPLETED" ? "done" : i < done + inWork ? "work" : "todo"} />)}</div>
        </div>
      )}
      <div className={`${s.glass} ${s.ticker}`}>
        {d.recent.slice(0, 4).map((r) => {
          const key = `activity.${r.action.replace(/^workforce\./, "")}`;
          return <span key={r.id}>● {tw.has(key) ? tw(key) : r.action}</span>;
        })}
      </div>
    </div>
  );
}

// ================================================================== new goal
function GoalDialog({ open, onOpenChange, ready }: { open: boolean; onOpenChange: (v: boolean) => void; ready: boolean }) {
  const t = useTranslations("workforce.room");
  const tc = useTranslations("common");
  const a = useWorkforceActions();
  const fail = useFail();
  const toast = useToasts((x) => x.push);
  const [prompt, setPrompt] = useState("");
  const examples = [t("ex1"), t("ex2"), t("ex3")];
  const go = (e: React.FormEvent) => {
    e.preventDefault();
    if (!prompt.trim() || !ready) return;
    a.submitObjective.mutate({ prompt }, { onError: fail, onSuccess: () => { toast("success", t("goalSent")); setPrompt(""); onOpenChange(false); } });
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent closeLabel={tc("close")}>
        <form onSubmit={go} className={s.modalBox}>
          <DialogTitle>{t("goalTitle")}</DialogTitle>
          <DialogDescription>{t("goalHelp")}</DialogDescription>
          <Label htmlFor="room-goal">{t("goalLabel")}</Label>
          <Textarea id="room-goal" rows={5} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder={t("goalPlaceholder")} />
          <div className={s.exs}>{examples.map((x) => <button type="button" key={x} className={s.ex} onClick={() => setPrompt(x)}>{x}</button>)}</div>
          {!ready && <p role="alert" className="text-sm text-destructive">{t("managerMissing")}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>{tc("cancel")}</Button>
            <Button type="submit" disabled={!prompt.trim() || !ready || a.submitObjective.isPending}>{t("launch")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
