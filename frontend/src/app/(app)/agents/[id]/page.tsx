"use client";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Pencil } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";
import { useParams } from "next/navigation";
import { AgentStateBadge } from "@/components/agent-state-badge";
import { useCan } from "@/components/providers";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAgent } from "@/lib/agents";
import { api } from "@/lib/api";

type Version = { version: number; snapshot: Record<string, unknown>; changed_by_user_id: number | null; created_at: string };
type Transition = { from: string | null; to: string; actor: string; reason: string; ts: string };

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="grid grid-cols-3 gap-3 py-2 text-sm">
      <dt className="text-muted-foreground">{k}</dt>
      <dd className="col-span-2 break-words">{v}</dd>
    </div>
  );
}

export default function AgentDetailsPage() {
  const t = useTranslations("agents");
  const tf = useTranslations("agents.form");
  const td = useTranslations("agents.details");
  const tc = useTranslations("common");
  const locale = useLocale();
  const nl = locale === "ar" ? "ar-u-nu-latn" : "en";
  const can = useCan();
  const { id } = useParams<{ id: string }>();
  const agent = useAgent(Number(id));
  const versions = useQuery({ queryKey: ["agent", Number(id), "versions"], queryFn: () => api<Version[]>(`/api/agents/${id}/versions`) });
  const transitions = useQuery({ queryKey: ["agent", Number(id), "transitions"], queryFn: () => api<Transition[]>(`/api/agents/${id}/transitions`) });

  if (agent.isLoading) return <Skeleton className="mx-auto h-96 max-w-5xl" />;
  if (!agent.data) return <Card role="alert" className="mx-auto max-w-5xl p-6 text-center">{tc("notFound")}</Card>;
  const a = agent.data;
  const dt = (s: string) => new Date(s).toLocaleString(nl);

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link href="/agents" className="mb-2 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
            <ArrowLeft className="size-4 rtl:-scale-x-100" aria-hidden />{t("title")}
          </Link>
          <h1 className="flex flex-wrap items-center gap-3 text-2xl font-bold">{a.name} <AgentStateBadge state={a.state} /></h1>
          <p className="font-mono text-xs text-muted-foreground" dir="ltr">{a.identifier}</p>
        </div>
        {can("agents.write") && !a.archived_at && (
          <Button asChild><Link href={`/agents/${a.id}/edit`}><Pencil aria-hidden />{tc("edit")}</Link></Button>
        )}
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>{td("overview")}</CardTitle></CardHeader>
          <CardContent>
            <dl className="divide-y">
              <Row k={tf("role")} v={<span dir="ltr">{a.role}</span>} />
              <Row k={tf("description")} v={<bdi>{a.description || "—"}</bdi>} />
              <Row k={td("enabled")} v={a.is_enabled ? tc("yes") : tc("no")} />
              <Row k={td("configVersion")} v={a.config_version} />
              <Row k={td("createdAt")} v={dt(a.created_at)} />
              {a.last_error && <Row k={td("lastError")} v={<span className="text-destructive">{a.last_error}</span>} />}
            </dl>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{td("configuration")}</CardTitle></CardHeader>
          <CardContent>
            <dl className="divide-y">
              <Row k={tf("engine")} v={tf(`engines.${a.execution_engine}`)} />
              <Row k={tf("model")} v={<span className="font-mono text-xs" dir="ltr">{a.model ?? "—"}</span>} />
              <Row k={tf("fallbackModel")} v={<span className="font-mono text-xs" dir="ltr">{a.fallback_model ?? "—"}</span>} />
              <Row k={tf("securityProfile")} v={tf(`profiles.${a.security_profile}`)} />
              <Row k={tf("tools")} v={<span className="flex flex-wrap gap-1" dir="ltr">{a.tool_permissions.length ? a.tool_permissions.map((x) => <code key={x} className="rounded bg-muted px-1.5 py-0.5 text-xs">{x}</code>) : "—"}</span>} />
              <Row k={td("budgets")} v={<span dir="ltr">{a.token_budget ?? td("unlimited")} tok · {a.cost_budget_usd ? `$${a.cost_budget_usd}` : td("unlimited")}</span>} />
              <Row k={tf("timeout")} v={a.timeout_seconds} />
              <Row k={tf("concurrency")} v={a.concurrency_limit} />
              <Row k={tf("priority")} v={a.priority} />
            </dl>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader><CardTitle>{tf("systemInstructions")}</CardTitle></CardHeader>
        <CardContent><pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-3 font-mono text-xs" dir="auto" tabIndex={0}>{a.system_instructions || "—"}</pre></CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>{td("transitions")}</CardTitle></CardHeader>
          <CardContent>
            {transitions.data?.length ? (
              <ol className="flex flex-col divide-y" data-testid="agent-transitions">
                {transitions.data.map((x, i) => (
                  <li key={i} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                    {x.from && <AgentStateBadge state={x.from} />}
                    {x.from && <ArrowLeft className="size-3.5 text-muted-foreground ltr:rotate-180" aria-hidden />}
                    <AgentStateBadge state={x.to} />
                    <span className="ms-auto text-xs text-muted-foreground"><span dir="ltr">{x.actor}</span> · {dt(x.ts)}</span>
                  </li>
                ))}
              </ol>
            ) : <p className="text-sm text-muted-foreground">{td("noTransitions")}</p>}
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>{td("versions")}</CardTitle></CardHeader>
          <CardContent>
            {versions.data?.length ? (
              <ol className="flex flex-col divide-y">
                {versions.data.map((v) => (
                  <li key={v.version} className="py-2 text-sm">
                    <details>
                      <summary className="cursor-pointer">{td("version", { v: v.version })} · <span className="text-xs text-muted-foreground">{dt(v.created_at)}</span></summary>
                      <pre className="mt-2 max-h-48 overflow-auto rounded-md bg-muted p-2 font-mono text-xs" dir="ltr" tabIndex={0}>{JSON.stringify(v.snapshot, null, 2)}</pre>
                    </details>
                  </li>
                ))}
              </ol>
            ) : <p className="text-sm text-muted-foreground">{td("noVersions")}</p>}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
