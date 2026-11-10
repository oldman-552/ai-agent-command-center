"use client";
import { useTranslations } from "next-intl";
import { Skills } from "@/components/integrations-panels";

export default function SkillsPage() {
  const t = useTranslations("integrations");
  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold">{t("skillsTitle")}</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">{t("skillsSubtitle")}</p>
      </div>
      <Skills />
    </div>
  );
}
