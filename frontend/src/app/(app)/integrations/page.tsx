"use client";
import { KeyRound, Network, Server } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { A2AKeys, Keys, Servers } from "@/components/integrations-panels";
import { useCan } from "@/components/providers";
import { Button } from "@/components/ui/button";

const TABS = ["servers", "keys", "a2a"] as const;
type Tab = (typeof TABS)[number];
const ICON = { servers: Server, keys: KeyRound, a2a: Network };

export default function IntegrationsPage() {
  const t = useTranslations("integrations");
  const can = useCan();
  const [tab, setTab] = useState<Tab>("servers");
  const visible = TABS.filter((x) => x === "servers" || (x === "keys" ? can("skills.manage") : can("security.manage")));
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>
      <div role="tablist" aria-label={t("title")} className="flex flex-wrap gap-2 border-b pb-2">
        {visible.map((x) => {
          const Icon = ICON[x];
          return (
            <Button key={x} role="tab" id={`tab-${x}`} aria-selected={tab === x} aria-controls={`panel-${x}`}
              variant={tab === x ? "default" : "ghost"} onClick={() => setTab(x)} data-testid={`tab-${x}`}>
              <Icon aria-hidden />{t(`tabs.${x}`)}
            </Button>
          );
        })}
      </div>
      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
        {tab === "servers" && <Servers />}
        {tab === "keys" && <Keys />}
        {tab === "a2a" && <A2AKeys />}
      </div>
    </div>
  );
}
