"use client";
import { Cpu, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useRuntime } from "@/lib/admin";

export default function RuntimePage() {
  const t = useTranslations("runtime");
  const te = useTranslations("executions");
  const fmt = useFormatter();
  const rt = useRuntime();
  const d = rt.data;
  const open = d?.circuits.filter((c) => c.state !== "closed") ?? [];
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>
      {rt.isLoading && <Skeleton className="h-40" aria-busy="true" />}
      {d && (
        <>
          <section aria-labelledby="rt-engines" className="flex flex-col gap-3">
            <h2 id="rt-engines" className="text-lg font-semibold">{t("engines")}</h2>
            <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="rt-engines">
              {d.engines.map((e) => (
                <li key={e.kind}>
                  <Card className="flex flex-col gap-1 p-4">
                    <p className="flex items-center gap-2 font-semibold"><Cpu className="size-4 text-muted-foreground" aria-hidden /><span className="font-mono" dir="ltr">{e.kind}</span></p>
                    <p className="text-sm text-muted-foreground">{t.has(`engineHelp.${e.kind}`) ? t(`engineHelp.${e.kind}`) : ""}</p>
                    <p className="text-xs">{t("agentsUsing", { n: e.agents })}{e.is_mock ? ` · ${t("mock")}` : ""}</p>
                  </Card>
                </li>
              ))}
            </ul>
          </section>
          <section aria-labelledby="rt-exec" className="flex flex-col gap-3">
            <h2 id="rt-exec" className="text-lg font-semibold">{t("executions")}</h2>
            <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6">
              {Object.entries(d.executions).sort().map(([s, n]) => (
                <Card key={s} className="p-3">
                  <dt className="text-xs text-muted-foreground"><Link className="hover:underline" href={`/executions?status=${s}`}>{te.has(`statuses.${s}`) ? te(`statuses.${s}`) : s}</Link></dt>
                  <dd className="text-xl font-semibold tabular-nums">{n}</dd>
                </Card>
              ))}
            </dl>
          </section>
          <section aria-labelledby="rt-circ" className="flex flex-col gap-3">
            <h2 id="rt-circ" className="text-lg font-semibold">{t("circuits")}</h2>
            <p className="text-sm text-muted-foreground">{t("circuitsHelp")}</p>
            {open.length === 0 ? <Card className="p-6 text-center text-sm text-muted-foreground">{t("allClosed")}</Card> : (
              <ul className="flex flex-col gap-2" data-testid="rt-circuits">
                {open.map((c) => (
                  <li key={`${c.scope}-${c.ref_id}`}>
                    <Card className="flex flex-wrap items-start gap-3 p-3 text-sm">
                      <ShieldAlert className="mt-0.5 size-4 text-warning" aria-hidden />
                      <div className="min-w-0 flex-1">
                        <p className="font-medium">{t(`scope.${c.scope}`)} #{c.ref_id} — {t(`state.${c.state}`)}</p>
                        <p className="text-xs text-muted-foreground">{t("failures", { n: c.failures })}{c.opened_at ? ` · ${fmt.relativeTime(new Date(c.opened_at))}` : ""}</p>
                        {c.last_failure && <p className="break-words text-xs" dir="auto">{c.last_failure}</p>}
                      </div>
                    </Card>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  );
}
