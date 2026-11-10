"use client";
import { useQuery } from "@tanstack/react-query";
import { BadgeCheck, CheckCircle2, Gauge, RotateCcw, XCircle } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

type Profile = { key: string | number | null; label?: string | null; evaluations: number; approve_rate: number | null;
  avg_overall: number | null; avg_task_completion: number | null; avg_test_pass_rate: number | null;
  last_evaluated_at: string; trusted: boolean };
type Recent = { id: number; task_id: number | null; task_title: string | null; agent_name: string | null;
  task_type: string | null; model: string | null; decision: "approve" | "revise" | "reject"; overall: number | null; created_at: string };

const GROUPS = ["agent", "role", "provider", "model", "task_type", "project"] as const;
const DECISION = { approve: { icon: CheckCircle2, tone: "text-success" }, revise: { icon: RotateCcw, tone: "text-warning" },
  reject: { icon: XCircle, tone: "text-destructive" } } as const;

type ProfileList = { min_samples: number; items: Profile[] };
const fetchProfiles = (group: string) => api<ProfileList>(`/api/evaluations/profiles?group=${group}`);
const fetchRecent = () => api<Recent[]>("/api/evaluations?limit=20");

export default function EvaluationsRoute() {
  return <Suspense><EvaluationsPage /></Suspense>;
}

/** A percentage as text plus a bar (the bar is decorative; the number carries the meaning). */
function Pct({ v }: { v: number | null }) {
  if (v === null) return <span className="text-muted-foreground">—</span>;
  const n = Math.round(v * 100);
  return (
    <span className="flex items-center gap-2">
      <span className="w-10 tabular-nums">{n}%</span>
      <span className="h-1.5 w-20 overflow-hidden rounded-full bg-muted" aria-hidden>
        <span className="block h-full rounded-full bg-primary" style={{ width: `${n}%` }} />
      </span>
    </span>
  );
}

function EvaluationsPage() {
  const t = useTranslations("evaluations");
  const tc = useTranslations("common");
  const fmt = useFormatter();
  const sp = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const group = (GROUPS as readonly string[]).includes(sp.get("group") ?? "") ? sp.get("group")! : "agent";
  const profiles = useQuery({ queryKey: ["eval-profiles", group], queryFn: () => fetchProfiles(group) });
  const recent = useQuery({ queryKey: ["eval-recent"], queryFn: fetchRecent, refetchInterval: 10_000 });

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>

      <Card className="flex flex-col gap-4 p-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <h2 className="flex items-center gap-2 text-lg font-semibold"><Gauge className="size-5 text-primary" aria-hidden />{t("profiles")}</h2>
          <div className="flex min-w-44 flex-col gap-1.5">
            <Label htmlFor="e-group">{t("groupBy")}</Label>
            <NativeSelect id="e-group" value={group} onChange={(e) => {
              const next = new URLSearchParams(sp.toString());
              next.set("group", e.target.value);
              router.replace(`${pathname}?${next}`, { scroll: false });
            }}>
              {GROUPS.map((g) => <option key={g} value={g}>{t(`groups.${g}`)}</option>)}
            </NativeSelect>
          </div>
        </div>
        {profiles.data && <p className="text-xs text-muted-foreground">{t("trustedHelp", { n: profiles.data.min_samples })}</p>}
        {profiles.isLoading && <Skeleton className="h-40" aria-busy="true" />}
        {profiles.isError && <p role="alert" className="text-destructive">{tc("error")}</p>}
        {profiles.data && profiles.data.items.length === 0 && <p className="text-muted-foreground">{t("empty")}</p>}
        {profiles.data && profiles.data.items.length > 0 && (
          <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={t("profiles")}>
            <table className="w-full min-w-[720px] text-sm" data-testid="profiles-table">
              <thead className="border-b text-xs text-muted-foreground">
                <tr>{(["key", "evaluations", "approve_rate", "avg_task_completion", "avg_test_pass_rate", "last"] as const).map((c) => (
                  <th key={c} scope="col" className="px-3 py-2 text-start font-medium">{c === "key" ? t(`groups.${group}`) : t(`columns.${c}`)}</th>
                ))}</tr>
              </thead>
              <tbody className="divide-y">
                {profiles.data.items.map((p, i) => (
                  <tr key={i}>
                    <td className="px-3 py-2 font-medium" dir="auto">
                      <span className="inline-flex items-center gap-1.5">
                        {p.label ?? (p.key === null ? "—" : String(p.key))}
                        {p.trusted && <BadgeCheck className="size-4 text-success" aria-label={t("trusted")} role="img" />}
                      </span>
                    </td>
                    <td className="px-3 py-2 tabular-nums">{p.evaluations}</td>
                    <td className="px-3 py-2"><Pct v={p.approve_rate} /></td>
                    <td className="px-3 py-2"><Pct v={p.avg_task_completion} /></td>
                    <td className="px-3 py-2"><Pct v={p.avg_test_pass_rate} /></td>
                    <td className="px-3 py-2 text-muted-foreground">{fmt.relativeTime(new Date(p.last_evaluated_at))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card className="flex flex-col gap-3 p-4">
        <h2 className="text-lg font-semibold">{t("recent")}</h2>
        {recent.data && recent.data.length === 0 && <p className="text-muted-foreground">{t("empty")}</p>}
        <ul className="flex flex-col divide-y" data-testid="recent-evaluations">
          {recent.data?.map((r) => {
            const d = DECISION[r.decision];
            return (
              <li key={r.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2 text-sm">
                <span className="inline-flex w-36 items-center gap-1.5 font-medium"><d.icon className={cn("size-4", d.tone)} aria-hidden />{t(`decision.${r.decision}`)}</span>
                {r.task_id ? <Link className="min-h-10 content-center text-primary underline-offset-2 hover:underline" href={`/tasks?task=${r.task_id}`} dir="auto">{r.task_title ?? `#${r.task_id}`}</Link> : <span>—</span>}
                <span className="text-muted-foreground" dir="auto">{r.agent_name ?? "—"}</span>
                {r.overall !== null && <span className="tabular-nums text-muted-foreground">{t("overall", { n: Math.round(r.overall * 100) })}</span>}
                <span className="ms-auto text-xs text-muted-foreground">{fmt.relativeTime(new Date(r.created_at))}</span>
              </li>
            );
          })}
        </ul>
      </Card>
    </div>
  );
}
