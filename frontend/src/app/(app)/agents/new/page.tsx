"use client";
import { useTranslations } from "next-intl";
import { AgentForm } from "@/components/agent-form";

export default function NewAgentPage() {
  const t = useTranslations("agents.form");
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <h1 className="text-2xl font-bold">{t("createTitle")}</h1>
      <AgentForm />
    </div>
  );
}
