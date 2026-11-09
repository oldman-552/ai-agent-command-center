"use client";
import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { AlertCircle, Loader2, Save } from "lucide-react";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { type Agent, useAgents, useProvidersLite, useToolCatalog } from "@/lib/agents";
import { api, ApiError } from "@/lib/api";
import { useToasts } from "@/lib/toast";

const csv = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);
const optInt = z.union([z.literal(""), z.coerce.number().int().positive()]);

const schema = z.object({
  name: z.string().trim().min(1).max(128),
  identifier: z.union([z.literal(""), z.string().regex(/^[a-z0-9][a-z0-9-]*$/).max(64)]),
  role: z.string().trim().min(1).max(64),
  description: z.string().max(4000),
  avatar: z.union([z.literal(""), z.string().regex(/^[a-z0-9_-]+$/).max(64)]),
  system_instructions: z.string().max(50000),
  provider_id: z.string(),
  model: z.string().max(128),
  fallback_provider_id: z.string(),
  fallback_model: z.string().max(128),
  execution_engine: z.enum(["api", "cli", "browser", "a2a"]),
  security_profile: z.enum(["read_only", "reviewer", "developer", "manager"]),
  tool_permissions: z.array(z.string()),
  skills: z.string(),
  token_budget: optInt,
  cost_budget_usd: z.union([z.literal(""), z.coerce.number().nonnegative()]),
  timeout_seconds: z.coerce.number().int().min(1).max(86400),
  max_attempts: z.coerce.number().int().min(1).max(10),
  concurrency_limit: z.coerce.number().int().min(1).max(64),
  priority: z.coerce.number().int().min(1).max(10),
  supervisor_agent_id: z.string(),
  task_types: z.string(),
});
type FormValues = z.input<typeof schema>;

function toForm(a?: Agent): FormValues {
  return {
    name: a?.name ?? "", identifier: a?.identifier ?? "", role: a?.role ?? "", description: a?.description ?? "",
    avatar: a?.avatar ?? "", system_instructions: a?.system_instructions ?? "",
    provider_id: a?.provider_id ? String(a.provider_id) : "", model: a?.model ?? "",
    fallback_provider_id: a?.fallback_provider_id ? String(a.fallback_provider_id) : "", fallback_model: a?.fallback_model ?? "",
    execution_engine: a?.execution_engine ?? "api", security_profile: a?.security_profile ?? "developer",
    tool_permissions: a?.tool_permissions ?? ["repo.read"], skills: (a?.skills ?? []).join(", "),
    token_budget: a?.token_budget ? String(a.token_budget) : "", cost_budget_usd: a?.cost_budget_usd ?? "",
    timeout_seconds: a?.timeout_seconds ?? 900, max_attempts: a?.retry_policy.max_attempts ?? 3,
    concurrency_limit: a?.concurrency_limit ?? 1, priority: a?.priority ?? 5,
    supervisor_agent_id: a?.supervisor_agent_id ? String(a.supervisor_agent_id) : "",
    task_types: (a?.context_policy?.task_types ?? []).join(", "),
  };
}

function Field({ id, label, help, error, children }: { id: string; label: string; help?: string; error?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {help && !error && <p id={`${id}-help`} className="text-xs text-muted-foreground">{help}</p>}
      {error && <p id={`${id}-err`} className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

export function AgentForm({ agent }: { agent?: Agent }) {
  const t = useTranslations("agents.form");
  const ta = useTranslations("agents");
  const tc = useTranslations("common");
  const router = useRouter();
  const qc = useQueryClient();
  const toast = useToasts((s) => s.push);
  const providers = useProvidersLite();
  const catalog = useToolCatalog();
  const others = useAgents({});
  const [serverError, setServerError] = useState<string | null>(null);
  const { register, handleSubmit, watch, setValue, formState: { errors, isSubmitting } } = useForm<FormValues>({
    resolver: zodResolver(schema), defaultValues: toForm(agent),
  });

  const profile = watch("security_profile");
  const selected = watch("tool_permissions");
  const providerId = watch("provider_id");
  const fbProviderId = watch("fallback_provider_id");
  const ceiling = new Set(catalog.data?.profile_ceiling[profile] ?? []);

  // drop tools that the newly selected profile no longer allows
  useEffect(() => {
    if (!catalog.data) return;
    const allowed = selected.filter((x) => ceiling.has(x));
    if (allowed.length !== selected.length) setValue("tool_permissions", allowed);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile, catalog.data]);

  const modelsOf = (pid: string) => providers.data?.find((p) => String(p.id) === pid)?.models ?? [];
  const err = (k: keyof FormValues) => (errors[k] ? t("invalid") : undefined);

  async function onSubmit(raw: FormValues) {
    setServerError(null);
    const v = schema.parse(raw);
    const body: Record<string, unknown> = {
      name: v.name, role: v.role, description: v.description, avatar: v.avatar || null,
      system_instructions: v.system_instructions,
      provider_id: v.provider_id ? Number(v.provider_id) : null, model: v.model || null,
      fallback_provider_id: v.fallback_provider_id ? Number(v.fallback_provider_id) : null, fallback_model: v.fallback_model || null,
      execution_engine: v.execution_engine, security_profile: v.security_profile, tool_permissions: v.tool_permissions,
      skills: csv(v.skills), token_budget: v.token_budget === "" ? null : v.token_budget,
      cost_budget_usd: v.cost_budget_usd === "" ? null : v.cost_budget_usd, timeout_seconds: v.timeout_seconds,
      retry_policy: { ...(agent?.retry_policy ?? { backoff_seconds: 5, backoff_multiplier: 2 }), max_attempts: v.max_attempts },
      concurrency_limit: v.concurrency_limit, priority: v.priority,
      supervisor_agent_id: v.supervisor_agent_id ? Number(v.supervisor_agent_id) : null,
      context_policy: { ...(agent?.context_policy ?? {}), task_types: csv(v.task_types) },
    };
    if (!agent && v.identifier) body.identifier = v.identifier;
    try {
      const saved = agent
        ? await api<Agent>(`/api/agents/${agent.id}`, { method: "PATCH", body: JSON.stringify(body) })
        : await api<Agent>("/api/agents", { method: "POST", body: JSON.stringify(body) });
      qc.invalidateQueries({ queryKey: ["agents"] });
      qc.invalidateQueries({ queryKey: ["agent", saved.id] });
      toast("success", tc("saved"));
      router.push(`/agents/${saved.id}`);
    } catch (e) {
      const code = e instanceof ApiError ? e.code : "network";
      if (code.startsWith("tools_not_allowed_for_profile:")) setServerError(ta("errors.tools", { tools: code.split(":")[1] }));
      else setServerError(ta.has(`errors.${code}`) ? ta(`errors.${code}`) : tc("requestFailed", { code }));
    }
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)} noValidate className="flex flex-col gap-4">
      {serverError && (
        <div role="alert" className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>{serverError}</span>
        </div>
      )}

      <Card>
        <CardHeader><CardTitle>{t("sections.identity")}</CardTitle></CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <Field id="name" label={t("name")} error={errors.name && t("required")}>
            <Input id="name" dir="auto" aria-invalid={!!errors.name || undefined} {...register("name")} />
          </Field>
          <Field id="identifier" label={t("identifier")} help={t("identifierHelp")} error={err("identifier")}>
            <Input id="identifier" dir="ltr" disabled={!!agent} aria-describedby="identifier-help" {...register("identifier")} />
          </Field>
          <Field id="role" label={t("role")} error={errors.role && t("required")}>
            <Input id="role" dir="ltr" aria-invalid={!!errors.role || undefined} {...register("role")} />
          </Field>
          <Field id="avatar" label={t("avatar")} error={err("avatar")}>
            <Input id="avatar" dir="ltr" {...register("avatar")} />
          </Field>
          <div className="md:col-span-2">
            <Field id="description" label={t("description")}>
              <Textarea id="description" dir="auto" className="min-h-16" {...register("description")} />
            </Field>
          </div>
          <div className="md:col-span-2">
            <Field id="system_instructions" label={t("systemInstructions")}>
              <Textarea id="system_instructions" dir="auto" className="min-h-40 font-mono text-xs" {...register("system_instructions")} />
            </Field>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>{t("sections.model")}</CardTitle></CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-2">
          <Field id="provider_id" label={t("provider")}>
            <NativeSelect id="provider_id" {...register("provider_id")}>
              <option value="">{t("noProvider")}</option>
              {providers.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </NativeSelect>
          </Field>
          <Field id="model" label={t("model")}>
            <NativeSelect id="model" {...register("model")} disabled={!providerId}>
              <option value="">—</option>
              {modelsOf(providerId).map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
            </NativeSelect>
          </Field>
          <Field id="fallback_provider_id" label={t("fallbackProvider")}>
            <NativeSelect id="fallback_provider_id" {...register("fallback_provider_id")}>
              <option value="">{t("noProvider")}</option>
              {providers.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </NativeSelect>
          </Field>
          <Field id="fallback_model" label={t("fallbackModel")}>
            <NativeSelect id="fallback_model" {...register("fallback_model")} disabled={!fbProviderId}>
              <option value="">—</option>
              {modelsOf(fbProviderId).map((m) => <option key={m.name} value={m.name}>{m.name}</option>)}
            </NativeSelect>
          </Field>
          <Field id="execution_engine" label={t("engine")}>
            <NativeSelect id="execution_engine" {...register("execution_engine")}>
              {(["api", "cli", "browser", "a2a"] as const).map((e) => <option key={e} value={e}>{t(`engines.${e}`)}</option>)}
            </NativeSelect>
          </Field>
          <Field id="supervisor_agent_id" label={t("supervisor")}>
            <NativeSelect id="supervisor_agent_id" {...register("supervisor_agent_id")}>
              <option value="">—</option>
              {others.data?.filter((o) => o.id !== agent?.id).map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </NativeSelect>
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>{t("sections.permissions")}</CardTitle></CardHeader>
        <CardContent className="flex flex-col gap-4">
          <Field id="security_profile" label={t("securityProfile")}>
            <NativeSelect id="security_profile" {...register("security_profile")}>
              {(["read_only", "reviewer", "developer", "manager"] as const).map((p) => <option key={p} value={p}>{t(`profiles.${p}`)}</option>)}
            </NativeSelect>
          </Field>
          <fieldset>
            <legend className="text-sm font-medium">{t("tools")}</legend>
            <p className="mt-1 text-xs text-muted-foreground">{t("toolsHelp")}</p>
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              {Object.entries(catalog.data?.tools ?? {}).map(([key, desc]) => {
                const allowed = ceiling.has(key);
                return (
                  <label key={key} className={`flex min-h-10 items-start gap-2 rounded-md border p-2 text-sm ${allowed ? "" : "opacity-50"}`}>
                    <input type="checkbox" value={key} disabled={!allowed} className="mt-1 size-4 accent-[var(--primary)]" {...register("tool_permissions")} />
                    <span>
                      <span className="font-mono text-xs" dir="ltr">{key}</span>
                      <span className="block text-xs text-muted-foreground" dir="ltr">{desc}</span>
                    </span>
                  </label>
                );
              })}
            </div>
          </fieldset>
          <Field id="skills" label={t("skills")} help={t("skillsHelp")}>
            <Input id="skills" dir="ltr" {...register("skills")} />
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>{t("sections.limits")}</CardTitle></CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Field id="token_budget" label={t("tokenBudget")} error={err("token_budget")}>
            <Input id="token_budget" type="number" min={1} dir="ltr" {...register("token_budget")} />
          </Field>
          <Field id="cost_budget_usd" label={t("costBudget")} error={err("cost_budget_usd")}>
            <Input id="cost_budget_usd" type="number" min={0} step="0.01" dir="ltr" {...register("cost_budget_usd")} />
          </Field>
          <Field id="timeout_seconds" label={t("timeout")} error={err("timeout_seconds")}>
            <Input id="timeout_seconds" type="number" min={1} dir="ltr" {...register("timeout_seconds")} />
          </Field>
          <Field id="max_attempts" label={t("maxAttempts")} error={err("max_attempts")}>
            <Input id="max_attempts" type="number" min={1} max={10} dir="ltr" {...register("max_attempts")} />
          </Field>
          <Field id="concurrency_limit" label={t("concurrency")} error={err("concurrency_limit")}>
            <Input id="concurrency_limit" type="number" min={1} max={64} dir="ltr" {...register("concurrency_limit")} />
          </Field>
          <Field id="priority" label={t("priority")} error={err("priority")}>
            <Input id="priority" type="number" min={1} max={10} dir="ltr" {...register("priority")} />
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>{t("sections.context")}</CardTitle></CardHeader>
        <CardContent>
          <Field id="task_types" label={t("taskTypes")} help={t("taskTypesHelp")}>
            <Input id="task_types" dir="ltr" {...register("task_types")} />
          </Field>
        </CardContent>
      </Card>

      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={() => router.back()}>{tc("cancel")}</Button>
        <Button type="submit" disabled={isSubmitting} data-testid="agent-submit">
          {isSubmitting ? <Loader2 className="animate-spin" aria-hidden /> : <Save aria-hidden />}
          {isSubmitting ? tc("saving") : tc("save")}
        </Button>
      </div>
    </form>
  );
}
