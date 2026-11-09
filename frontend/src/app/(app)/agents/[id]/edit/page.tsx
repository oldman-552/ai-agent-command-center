"use client";
import { useTranslations } from "next-intl";
import { useParams } from "next/navigation";
import { AgentForm } from "@/components/agent-form";
import { Skeleton } from "@/components/ui/skeleton";
import { useAgent } from "@/lib/agents";

export default function EditAgentPage() {
  const t = useTranslations("agents.form");
  const { id } = useParams<{ id: string }>();
  const agent = useAgent(Number(id));
  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <h1 className="text-2xl font-bold">{t("editTitle")}</h1>
      {agent.data ? <AgentForm agent={agent.data} /> : <Skeleton className="h-96" />}
    </div>
  );
}
