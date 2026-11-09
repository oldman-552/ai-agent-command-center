"use client";
import { LayoutTemplate, Plus } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useCan } from "@/components/providers";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { type Agent, useTemplates } from "@/lib/agents";
import { api, ApiError } from "@/lib/api";
import { useToasts } from "@/lib/toast";

export default function TemplatesPage() {
  const t = useTranslations("templates");
  const tf = useTranslations("agents.form");
  const tc = useTranslations("common");
  const locale = useLocale();
  const can = useCan();
  const router = useRouter();
  const toast = useToasts((s) => s.push);
  const templates = useTemplates();
  const [busy, setBusy] = useState<number | null>(null);

  async function use(id: number, name: string) {
    setBusy(id);
    try {
      const a = await api<Agent>(`/api/agent-templates/${id}/instantiate?name=${encodeURIComponent(name)}&lang=${locale}`, { method: "POST" });
      toast("success", t("created"));
      router.push(`/agents/${a.id}/edit`);
    } catch (e) {
      toast("error", tc("requestFailed", { code: e instanceof ApiError ? e.code : "network" }));
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>
      {templates.isLoading && <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-40" />)}</div>}
      <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" data-testid="templates-grid">
        {templates.data?.map((x) => {
          const name = locale === "ar" ? x.name_ar : x.name_en;
          return (
            <li key={x.id}>
              <Card className="flex h-full flex-col gap-3 p-4">
                <div className="flex items-start gap-3">
                  <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary"><LayoutTemplate className="size-4" aria-hidden /></span>
                  <div className="min-w-0">
                    <h2 className="font-semibold">{name}</h2>
                    <p className="text-xs text-muted-foreground">{x.is_system ? t("system") : t("custom")} · {tf(`profiles.${x.security_profile}`)}</p>
                  </div>
                </div>
                <p className="flex-1 text-sm text-muted-foreground">{locale === "ar" ? x.description_ar : x.description_en}</p>
                <p className="flex flex-wrap gap-1" dir="ltr" aria-label={t("tools")}>
                  {x.tool_permissions.map((tool) => <code key={tool} className="rounded bg-muted px-1.5 py-0.5 text-xs">{tool}</code>)}
                </p>
                {can("agents.write") && (
                  <Button variant="outline" size="sm" disabled={busy !== null} onClick={() => use(x.id, name)}
                    aria-label={`${t("use")}: ${name}`}>
                    <Plus aria-hidden />{t("use")}
                  </Button>
                )}
              </Card>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
