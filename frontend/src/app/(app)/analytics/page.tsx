"use client";
import { Plus, Trash2 } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import { useState } from "react";
import { useCan } from "@/components/providers";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useBudgetActions, useBudgets, useUsage } from "@/lib/admin";
import { ApiError } from "@/lib/api";
import { useToasts } from "@/lib/toast";
import { cn } from "@/lib/utils";

const GROUPS = ["provider", "model", "agent", "project", "day"] as const;

export default function AnalyticsPage() {
  const t = useTranslations("analytics");
  const fmt = useFormatter();
  const [group, setGroup] = useState<(typeof GROUPS)[number]>("provider");
  const [days, setDays] = useState(30);
  const usage = useUsage(group, days);
  const items = usage.data?.items.filter((i) => !i.is_mock) ?? [];
  const maxTokens = Math.max(1, ...items.map((i) => i.input_tokens + i.output_tokens));
  const tot = usage.data?.totals;
  const usd = (v: string | null) => (v === null ? "—" : fmt.number(Number(v), { style: "currency", currency: "USD", maximumFractionDigits: 4 }));
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>
      <div className="flex flex-wrap gap-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="an-group">{t("groupBy")}</Label>
          <NativeSelect id="an-group" value={group} onChange={(e) => setGroup(e.target.value as typeof group)}>
            {GROUPS.map((g) => <option key={g} value={g}>{t(`groups.${g}`)}</option>)}
          </NativeSelect>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="an-days">{t("period")}</Label>
          <NativeSelect id="an-days" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[7, 30, 90, 365].map((d) => <option key={d} value={d}>{t("lastDays", { n: d })}</option>)}
          </NativeSelect>
        </div>
      </div>
      {tot && (
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4" data-testid="an-totals">
          <Card className="p-4"><dt className="text-xs text-muted-foreground">{t("requests")}</dt><dd className="text-xl font-semibold tabular-nums">{fmt.number(tot.requests)}</dd></Card>
          <Card className="p-4"><dt className="text-xs text-muted-foreground">{t("tokens")}</dt><dd className="text-xl font-semibold tabular-nums">{fmt.number(tot.tokens)}</dd></Card>
          <Card className="p-4"><dt className="text-xs text-muted-foreground">{t("cost")}</dt><dd className="text-xl font-semibold tabular-nums">{usd(tot.cost_usd)}</dd></Card>
          <Card className="p-4"><dt className="text-xs text-muted-foreground">{t("unpriced")}</dt><dd className="text-xl font-semibold tabular-nums">{fmt.number(tot.unpriced_requests)}</dd></Card>
        </dl>
      )}
      <p className="text-xs text-muted-foreground">{t("honesty")}</p>
      {usage.isLoading && <Skeleton className="h-40" aria-busy="true" />}
      {usage.data && (
        <Card className="overflow-x-auto" tabIndex={0} aria-label={t("tableLabel")}>
          <table className="w-full min-w-[720px] text-sm" data-testid="an-table">
            <caption className="sr-only">{t("tableLabel")}</caption>
            <thead className="border-b bg-muted/50 text-xs text-muted-foreground">
              <tr>
                {(["key", "requests", "tokens", "share", "cost", "unpriced", "latency", "retries"] as const).map((c) => (
                  <th key={c} scope="col" className={cn("px-3 py-2 font-medium", c === "key" || c === "share" ? "text-start" : "text-end")}>{c === "key" ? t(`groups.${group}`) : t(`cols.${c}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y">
              {items.length === 0 && <tr><td colSpan={8} className="px-3 py-6 text-center text-muted-foreground">{t("empty")}</td></tr>}
              {items.map((i) => {
                const tok = i.input_tokens + i.output_tokens;
                return (
                  <tr key={`${i.key}-${i.is_mock}`}>
                    <th scope="row" className="px-3 py-2 text-start font-medium" dir="auto">{i.label}</th>
                    <td className="px-3 py-2 text-end tabular-nums">{fmt.number(i.requests)}</td>
                    <td className="px-3 py-2 text-end tabular-nums">{fmt.number(tok)}</td>
                    <td className="px-3 py-2"><div className="h-2 w-32 rounded-full bg-muted" aria-hidden><div className="h-2 rounded-full bg-primary" style={{ width: `${(100 * tok) / maxTokens}%` }} /></div></td>
                    <td className="px-3 py-2 text-end tabular-nums">{usd(i.cost_usd)}</td>
                    <td className="px-3 py-2 text-end tabular-nums">{i.unpriced_requests}</td>
                    <td className="px-3 py-2 text-end tabular-nums">{i.avg_duration_ms !== null ? `${fmt.number(i.avg_duration_ms)} ms` : "—"}</td>
                    <td className="px-3 py-2 text-end tabular-nums">{i.retries}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Card>
      )}
      <Budgets />
    </div>
  );
}

function Budgets() {
  const t = useTranslations("analytics");
  const tc = useTranslations("common");
  const fmt = useFormatter();
  const can = useCan();
  const budgets = useBudgets();
  const a = useBudgetActions();
  const toast = useToasts((s) => s.push);
  const fail = (e: unknown) => toast("error", tc("requestFailed", { code: e instanceof ApiError ? e.code : "error" }));
  const [scope, setScope] = useState("global");
  const [scopeId, setScopeId] = useState("");
  const [period, setPeriod] = useState("monthly");
  const [usdLimit, setUsdLimit] = useState("");
  const [tokLimit, setTokLimit] = useState("");
  const manage = can("budgets.manage");
  return (
    <section aria-labelledby="bud-h" className="flex flex-col gap-3">
      <h2 id="bud-h" className="text-lg font-semibold">{t("budgets")}</h2>
      <p className="max-w-3xl text-sm text-muted-foreground">{t("budgetsHelp")}</p>
      {manage && (
        <Card className="p-4">
          <form className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6 lg:items-end" onSubmit={(e) => {
            e.preventDefault();
            a.create.mutate({ scope, scope_id: scope === "global" ? null : Number(scopeId), period,
              ...(usdLimit ? { limit_usd: usdLimit } : {}), ...(tokLimit ? { limit_tokens: Number(tokLimit) } : {}) },
            { onSuccess: () => { setUsdLimit(""); setTokLimit(""); }, onError: fail });
          }}>
            <div className="flex flex-col gap-1.5"><Label htmlFor="b-scope">{t("scope")}</Label>
              <NativeSelect id="b-scope" value={scope} onChange={(e) => setScope(e.target.value)}>
                {["global", "agent", "project", "task", "provider"].map((s) => <option key={s} value={s}>{t(`scopes.${s}`)}</option>)}
              </NativeSelect></div>
            <div className="flex flex-col gap-1.5"><Label htmlFor="b-id">{t("scopeId")}</Label>
              <Input id="b-id" type="number" min={1} disabled={scope === "global"} required={scope !== "global"} value={scopeId} onChange={(e) => setScopeId(e.target.value)} dir="ltr" /></div>
            <div className="flex flex-col gap-1.5"><Label htmlFor="b-period">{t("periodLabel")}</Label>
              <NativeSelect id="b-period" value={period} onChange={(e) => setPeriod(e.target.value)}>
                {["daily", "monthly", "total"].map((s) => <option key={s} value={s}>{t(`periods.${s}`)}</option>)}
              </NativeSelect></div>
            <div className="flex flex-col gap-1.5"><Label htmlFor="b-usd">{t("limitUsd")}</Label>
              <Input id="b-usd" inputMode="decimal" pattern="\d{0,8}(\.\d{0,4})?" value={usdLimit} onChange={(e) => setUsdLimit(e.target.value)} dir="ltr" /></div>
            <div className="flex flex-col gap-1.5"><Label htmlFor="b-tok">{t("limitTokens")}</Label>
              <Input id="b-tok" type="number" min={1} value={tokLimit} onChange={(e) => setTokLimit(e.target.value)} dir="ltr" /></div>
            <Button type="submit" disabled={a.create.isPending || (!usdLimit && !tokLimit)} data-testid="budget-add"><Plus aria-hidden />{t("addBudget")}</Button>
          </form>
        </Card>
      )}
      {budgets.data?.length === 0 && <p className="text-sm text-muted-foreground">{t("noBudgets")}</p>}
      <ul className="flex flex-col gap-2" data-testid="budget-list">
        {budgets.data?.map((b) => {
          const pct = b.limit_usd ? Number(b.spent_usd) / Number(b.limit_usd) : b.limit_tokens ? b.spent_tokens / b.limit_tokens : 0;
          return (
            <li key={b.id}>
              <Card className="flex flex-wrap items-center gap-3 p-3 text-sm">
                <div className="min-w-0 flex-1">
                  <p className="font-medium">{t(`scopes.${b.scope}`)}{b.scope_id ? ` #${b.scope_id}` : ""} · {t(`periods.${b.period}`)}{!b.enabled && ` · ${t("disabled")}`}</p>
                  <p className="text-xs text-muted-foreground tabular-nums">
                    {b.limit_usd && t("spentUsd", { spent: fmt.number(Number(b.spent_usd), { maximumFractionDigits: 4 }), limit: b.limit_usd })}
                    {b.limit_usd && b.limit_tokens && " · "}
                    {b.limit_tokens && t("spentTokens", { spent: fmt.number(b.spent_tokens), limit: fmt.number(b.limit_tokens) })}
                  </p>
                  <div className="mt-1 h-2 w-full max-w-sm rounded-full bg-muted" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.min(100, Math.round(pct * 100))} aria-label={t("used")}>
                    <div className={cn("h-2 rounded-full", b.exceeded ? "bg-destructive" : "bg-primary")} style={{ width: `${Math.min(100, pct * 100)}%` }} />
                  </div>
                </div>
                {b.exceeded && <span className="text-xs font-medium" data-testid="budget-exceeded">{t("exceeded")}</span>}
                {manage && <Button size="sm" variant="outline" onClick={() => a.remove.mutate(b.id, { onError: fail })} aria-label={`${t("deleteBudget")} #${b.id}`}><Trash2 aria-hidden /></Button>}
              </Card>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
