"use client";
import { AlertOctagon, AlertTriangle, Download, Info, ScrollText, Search } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { type AuditEntry, auditQuery, useAudit } from "@/lib/audit";
import { cn } from "@/lib/utils";

export default function AuditRoute() {
  return <Suspense><AuditPage /></Suspense>;
}

const LEVEL = {
  critical: { icon: AlertOctagon, tone: "text-destructive" },
  error: { icon: AlertOctagon, tone: "text-destructive" },
  warning: { icon: AlertTriangle, tone: "text-warning" },
  info: { icon: Info, tone: "text-primary" },
  debug: { icon: Info, tone: "text-muted-foreground" },
} as const;

function LevelLabel({ level }: { level: string }) {
  const t = useTranslations("audit");
  const l = LEVEL[level as keyof typeof LEVEL] ?? LEVEL.info;
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs font-medium">
      <l.icon className={cn("size-4", l.tone)} aria-hidden />{t.has(`levels.${level}`) ? t(`levels.${level}`) : level}
    </span>
  );
}

/** The message, or a compact "key=value" summary of the details when there is no message. */
function summary(e: AuditEntry): string {
  if (e.message) return e.message;
  return Object.entries(e.details).slice(0, 4)
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" · ").slice(0, 240);
}

function AuditPage() {
  const t = useTranslations("audit");
  const tc = useTranslations("common");
  const fmt = useFormatter();
  const sp = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const filters = {
    q: sp.get("q") ?? undefined, category: sp.get("category") ?? undefined, level: sp.get("level") ?? undefined,
    actor: sp.get("actor") ?? undefined, since: sp.get("since") ?? undefined, until: sp.get("until") ?? undefined,
  };
  const [q, setQ] = useState(filters.q ?? "");
  const set = (patch: Record<string, string | undefined>) => {
    const next = new URLSearchParams(sp.toString());
    for (const [k, v] of Object.entries(patch)) (v ? next.set(k, v) : next.delete(k));
    router.replace(`${pathname}?${next}`, { scroll: false });
  };
  const audit = useAudit(filters);
  const first = audit.data?.pages[0];
  const items = audit.data?.pages.flatMap((p) => p.items) ?? [];
  const [open, setOpen] = useState<AuditEntry | null>(null);

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{t("title")}</h1>
          <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
        </div>
        <Button variant="outline" asChild>
          <a href={`/api/audit/export.csv?${auditQuery(filters)}`} download data-testid="audit-export"><Download aria-hidden />{t("export")}</a>
        </Button>
      </div>

      <Card className="p-4">
        <form className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6" role="search" aria-label={t("filters.label")}
          onSubmit={(e) => { e.preventDefault(); set({ q: q.trim() || undefined }); }}>
          <div className="flex flex-col gap-1.5 lg:col-span-2">
            <Label htmlFor="a-q">{t("filters.search")}</Label>
            <div className="flex gap-2">
              <Input id="a-q" value={q} onChange={(e) => setQ(e.target.value)} dir="auto" maxLength={200} />
              <Button type="submit" variant="secondary" size="icon" aria-label={t("filters.search")}><Search aria-hidden /></Button>
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="a-cat">{t("filters.category")}</Label>
            <NativeSelect id="a-cat" value={filters.category ?? ""} onChange={(e) => set({ category: e.target.value || undefined })}>
              <option value="">{tc("all")}</option>
              {first?.categories.map((c) => <option key={c} value={c}>{t.has(`categories.${c}`) ? t(`categories.${c}`) : c}</option>)}
            </NativeSelect>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="a-level">{t("filters.level")}</Label>
            <NativeSelect id="a-level" value={filters.level ?? ""} onChange={(e) => set({ level: e.target.value || undefined })}>
              <option value="">{tc("all")}</option>
              {(["warning", "error", "critical"] as const).map((l) => <option key={l} value={l}>{t("filters.atLeast", { level: t(`levels.${l}`) })}</option>)}
            </NativeSelect>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="a-since">{t("filters.since")}</Label>
            <Input id="a-since" type="date" value={filters.since?.slice(0, 10) ?? ""} onChange={(e) => set({ since: e.target.value || undefined })} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="a-until">{t("filters.until")}</Label>
            <Input id="a-until" type="date" value={filters.until?.slice(0, 10) ?? ""} onChange={(e) => set({ until: e.target.value ? `${e.target.value}T23:59:59` : undefined })} />
          </div>
        </form>
      </Card>

      {first && <p className="text-sm text-muted-foreground" role="status" data-testid="audit-total">{t("total", { n: first.total })}</p>}
      {audit.isLoading && <Skeleton className="h-64" aria-busy="true" />}
      {audit.isError && <Card role="alert" className="p-6 text-center text-destructive">{tc("error")}</Card>}
      {first && items.length === 0 && (
        <Card className="flex flex-col items-center gap-3 p-10 text-center">
          <ScrollText className="size-8 text-muted-foreground" aria-hidden /><p className="text-muted-foreground">{t("empty")}</p>
        </Card>
      )}
      {items.length > 0 && (
        <Card className="overflow-x-auto" tabIndex={0} aria-label={t("title")}>
          <table className="w-full min-w-[820px] text-sm" data-testid="audit-table">
            <thead className="border-b bg-muted/50 text-xs text-muted-foreground">
              <tr>{(["time", "level", "actor", "action", "message"] as const).map((c) => (
                <th key={c} scope="col" className="px-4 py-3 text-start font-medium">{t(`columns.${c}`)}</th>
              ))}</tr>
            </thead>
            <tbody className="divide-y">
              {items.map((e) => (
                <tr key={e.id} className="hover:bg-muted/30">
                  <td className="whitespace-nowrap px-4 py-2 tabular-nums">{fmt.dateTime(new Date(e.ts), { dateStyle: "short", timeStyle: "medium" })}</td>
                  <td className="px-4 py-2"><LevelLabel level={e.level} /></td>
                  <td className="px-4 py-2 font-mono text-xs" dir="ltr">{e.actor}</td>
                  <td className="px-4 py-2">
                    <button type="button" className="min-h-10 text-start font-mono text-xs text-primary hover:underline" dir="ltr" onClick={() => setOpen(e)}>{e.action}</button>
                  </td>
                  <td className="max-w-md truncate px-4 py-2" dir="auto" title={summary(e)}>{summary(e) || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
      {audit.hasNextPage && (
        <Button variant="outline" className="self-center" disabled={audit.isFetchingNextPage} onClick={() => audit.fetchNextPage()}>
          {t("loadMore")}
        </Button>
      )}

      {open && (
        <Dialog open onOpenChange={(o) => !o && setOpen(null)}>
          <DialogContent closeLabel={tc("close")} className="max-h-[90vh] max-w-2xl overflow-y-auto">
            <DialogTitle className="pe-8 font-mono text-base" dir="ltr">{open.action}</DialogTitle>
            <DialogDescription className="flex flex-wrap items-center gap-3 text-sm">
              <LevelLabel level={open.level} />
              <span>{fmt.dateTime(new Date(open.ts), { dateStyle: "medium", timeStyle: "medium" })}</span>
              <span className="font-mono text-xs" dir="ltr">{open.actor}</span>
            </DialogDescription>
            {open.message && <p className="mt-3 text-sm" dir="auto">{open.message}</p>}
            <dl className="mt-3 grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
              {(["agent_id", "project_id", "task_id", "execution_id"] as const).map((k) => (
                <div key={k}><dt className="text-xs text-muted-foreground">{t(`fields.${k}`)}</dt><dd className="font-mono text-xs" dir="ltr">{open[k] ?? "—"}</dd></div>
              ))}
            </dl>
            <h3 className="mt-4 text-sm font-semibold">{t("fields.details")}</h3>
            <pre tabIndex={0} aria-label={t("fields.details")} className="mt-1 max-h-72 overflow-auto rounded-md bg-muted p-3 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" dir="ltr">{JSON.stringify(open.details, null, 2)}</pre>
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}
