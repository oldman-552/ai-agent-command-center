"use client";
import { ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import { useTranslations } from "next-intl";
import type { Assessment } from "@/lib/integrations";
import { cn } from "@/lib/utils";

const RISK = {
  high: { icon: ShieldAlert, tone: "text-destructive" },
  medium: { icon: ShieldQuestion, tone: "text-warning" },
  low: { icon: ShieldCheck, tone: "text-success" },
} as const;

export function RiskLabel({ risk }: { risk?: string | null }) {
  const t = useTranslations("integrations");
  const r = RISK[(risk ?? "medium") as keyof typeof RISK] ?? RISK.medium;
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium" data-testid="risk">
      <r.icon className={cn("size-3.5", r.tone)} aria-hidden />{risk ? t(`risk.${risk}`) : t("risk.unknown")}
    </span>
  );
}

const CATEGORIES = ["dangerous_commands", "credentials", "network", "filesystem", "dependencies", "instructions"] as const;

/** Category counts, then each finding with file:line — what a reviewer needs before enabling. */
export function AssessmentView({ a }: { a: Assessment }) {
  const t = useTranslations("integrations");
  return (
    <div className="flex flex-col gap-3 text-sm">
      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {CATEGORIES.map((c) => (
          <div key={c} className="rounded-md border p-2">
            <dt className="text-xs text-muted-foreground">{t(`categories.${c}`)}</dt>
            <dd className="text-lg font-semibold tabular-nums">{a.summary[c] ?? 0}</dd>
          </div>
        ))}
      </dl>
      {a.findings.length > 0 ? (
        <ul className="flex max-h-64 flex-col gap-1 overflow-y-auto rounded-md border p-2" tabIndex={0} aria-label={t("findings")}>
          {a.findings.map((f, i) => (
            <li key={i} className="flex flex-col">
              <span>{t(`categories.${f.category}`)} — <span dir="ltr">{f.label}</span></span>
              <span className="font-mono text-xs text-muted-foreground" dir="ltr">{f.file}{f.line ? `:${f.line}` : ""} {f.excerpt}</span>
            </li>
          ))}
        </ul>
      ) : <p className="text-muted-foreground">{t("noFindings")}</p>}
    </div>
  );
}
