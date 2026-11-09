"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, CircleDashed, FlaskConical, KeyRound, Pencil, Plug, Plus, Power, PowerOff, Search, Trash2, XCircle } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { useCan } from "@/components/providers";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError } from "@/lib/api";
import { useToasts } from "@/lib/toast";
import { cn } from "@/lib/utils";

type Model = {
  id: number; name: string; display_name: string | null; context_window: number | null; tier: number; enabled: boolean;
  input_price_per_mtok: string | null; output_price_per_mtok: string | null; cached_input_price_per_mtok: string | null;
};
type Provider = {
  id: number; name: string; kind: string; is_mock: boolean; enabled: boolean; base_url: string | null; status: string;
  priority: number; last_tested_at: string | null; last_error: string | null; has_credential: boolean;
  credential_hint: string | null; models: Model[];
};
type Kind = { kind: string; is_mock: boolean; needs_api_key: boolean; needs_base_url: boolean };

const STATUS_ICON = { ok: CheckCircle2, error: XCircle, untested: CircleDashed, rate_limited: AlertCircle } as const;
const STATUS_TONE = { ok: "text-success", error: "text-destructive", untested: "text-muted-foreground", rate_limited: "text-warning" } as const;

function useErr() {
  const t = useTranslations("providers");
  const tc = useTranslations("common");
  return (e: unknown) => {
    const code = e instanceof ApiError ? e.code : "network";
    return t.has(`errors.${code}`) ? t(`errors.${code}`) : tc("requestFailed", { code });
  };
}

function AddProviderDialog({ open, onOpenChange, kinds }: { open: boolean; onOpenChange: (o: boolean) => void; kinds: Kind[] }) {
  const t = useTranslations("providers");
  const tc = useTranslations("common");
  const qc = useQueryClient();
  const toast = useToasts((s) => s.push);
  const errMsg = useErr();
  const [kind, setKind] = useState("anthropic");
  const [error, setError] = useState<string | null>(null);
  const k = kinds.find((x) => x.kind === kind);
  const create = useMutation({
    mutationFn: (body: Record<string, unknown>) => api<Provider>("/api/providers", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["providers"] }); toast("success", tc("saved")); onOpenChange(false); },
    onError: (e) => setError(errMsg(e)),
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent closeLabel={tc("close")}>
        <DialogTitle>{t("add")}</DialogTitle>
        <form className="mt-4 flex flex-col gap-3" onSubmit={(e) => {
          e.preventDefault();
          setError(null);
          const fd = new FormData(e.currentTarget);
          const body: Record<string, unknown> = { name: String(fd.get("name")).trim(), kind };
          if (fd.get("base_url")) body.base_url = String(fd.get("base_url")).trim();
          if (fd.get("api_key")) body.api_key = String(fd.get("api_key")).trim();
          create.mutate(body);
        }}>
          {error && <p role="alert" className="rounded-md bg-destructive/10 p-2 text-sm text-destructive">{error}</p>}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="p-kind">{t("fields.kind")}</Label>
            <NativeSelect id="p-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
              {kinds.filter((x) => !x.is_mock).map((x) => <option key={x.kind} value={x.kind}>{t(`kinds.${x.kind}`)}</option>)}
            </NativeSelect>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="p-name">{t("fields.name")}</Label>
            <Input id="p-name" name="name" required dir="auto" />
          </div>
          {k?.needs_base_url && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="p-url">{t("fields.baseUrl")}</Label>
              <Input id="p-url" name="base_url" type="url" required dir="ltr" />
            </div>
          )}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="p-key">{t("fields.apiKey")}{!k?.needs_api_key && ` (${tc("optional")})`}</Label>
            <Input id="p-key" name="api_key" type="password" autoComplete="off" required={k?.needs_api_key} dir="ltr" aria-describedby="p-key-help" />
            <p id="p-key-help" className="text-xs text-muted-foreground">{t("fields.apiKeyHelp")}</p>
          </div>
          <p className="rounded-md bg-muted p-2 text-xs text-muted-foreground">{t("subscriptionNote")}</p>
          <div className="mt-2 flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>{tc("cancel")}</Button>
            <Button type="submit" disabled={create.isPending} data-testid="provider-submit">{tc("create")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function KeyDialog({ provider, onClose }: { provider: Provider; onClose: () => void }) {
  const t = useTranslations("providers");
  const tc = useTranslations("common");
  const qc = useQueryClient();
  const toast = useToasts((s) => s.push);
  const errMsg = useErr();
  const save = useMutation({
    mutationFn: (api_key: string) => api(`/api/providers/${provider.id}/credential`, { method: "PUT", body: JSON.stringify({ api_key }) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["providers"] }); toast("success", tc("saved")); onClose(); },
    onError: (e) => toast("error", errMsg(e)),
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")}>
        <DialogTitle>{t("setKey")}: {provider.name}</DialogTitle>
        <form className="mt-4 flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); save.mutate(String(new FormData(e.currentTarget).get("k"))); }}>
          <Label htmlFor="k">{t("fields.apiKey")}</Label>
          <Input id="k" name="k" type="password" autoComplete="off" minLength={8} required dir="ltr" aria-describedby="k-help" />
          <p id="k-help" className="text-xs text-muted-foreground">{t("fields.apiKeyHelp")}</p>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={onClose}>{tc("cancel")}</Button>
            <Button type="submit" disabled={save.isPending}>{tc("save")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ModelDialog({ provider, model, onClose }: { provider: Provider; model: Model | null; onClose: () => void }) {
  const t = useTranslations("providers.models");
  const tc = useTranslations("common");
  const qc = useQueryClient();
  const toast = useToasts((s) => s.push);
  const errMsg = useErr();
  const save = useMutation({
    mutationFn: (body: Record<string, unknown>) => model
      ? api(`/api/models/${model.id}`, { method: "PATCH", body: JSON.stringify(body) })
      : api(`/api/providers/${provider.id}/models`, { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["providers"] }); toast("success", tc("saved")); onClose(); },
    onError: (e) => toast("error", errMsg(e)),
  });
  const price = (v: FormDataEntryValue | null) => (v === null || String(v).trim() === "" ? null : String(v).trim());
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent closeLabel={tc("close")}>
        <DialogTitle>{model ? `${t("edit")}: ${model.name}` : t("add")}</DialogTitle>
        <form className="mt-4 grid gap-3 sm:grid-cols-2" onSubmit={(e) => {
          e.preventDefault();
          const fd = new FormData(e.currentTarget);
          const body: Record<string, unknown> = {
            input_price_per_mtok: price(fd.get("in")), output_price_per_mtok: price(fd.get("out")),
            cached_input_price_per_mtok: price(fd.get("cached")), enabled: fd.get("enabled") === "on",
          };
          if (!model) body.name = String(fd.get("name")).trim();
          if (fd.get("ctx")) body.context_window = Number(fd.get("ctx"));
          save.mutate(body);
        }}>
          {!model && (
            <div className="flex flex-col gap-1.5 sm:col-span-2">
              <Label htmlFor="m-name">{t("name")}</Label>
              <Input id="m-name" name="name" required dir="ltr" />
            </div>
          )}
          {([["in", "input", model?.input_price_per_mtok], ["out", "output", model?.output_price_per_mtok],
            ["cached", "cached", model?.cached_input_price_per_mtok]] as const).map(([id, key, v]) => (
            <div key={id} className="flex flex-col gap-1.5">
              <Label htmlFor={`m-${id}`}>{t(key)}</Label>
              <Input id={`m-${id}`} name={id} type="number" min={0} step="0.0001" defaultValue={v ?? ""} dir="ltr" aria-describedby="m-price-help" />
            </div>
          ))}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="m-ctx">{t("context")}</Label>
            <Input id="m-ctx" name="ctx" type="number" min={1} defaultValue={model?.context_window ?? ""} dir="ltr" />
          </div>
          <p id="m-price-help" className="text-xs text-muted-foreground sm:col-span-2">{t("perMtok")}. {t("priceHelp")}</p>
          <label className="flex items-center gap-2 text-sm sm:col-span-2">
            <input type="checkbox" name="enabled" defaultChecked={model?.enabled ?? true} className="size-4 accent-[var(--primary)]" />
            {t("enabled")}
          </label>
          <div className="flex justify-end gap-2 sm:col-span-2">
            <Button type="button" variant="outline" onClick={onClose}>{tc("cancel")}</Button>
            <Button type="submit" disabled={save.isPending} data-testid="model-submit">{tc("save")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function ProvidersPage() {
  const t = useTranslations("providers");
  const tm = useTranslations("providers.models");
  const tc = useTranslations("common");
  const locale = useLocale();
  const nl = locale === "ar" ? "ar-u-nu-latn" : "en";
  const can = useCan();
  const qc = useQueryClient();
  const toast = useToasts((s) => s.push);
  const errMsg = useErr();
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => api<Provider[]>("/api/providers") });
  const kinds = useQuery({ queryKey: ["provider-kinds"], queryFn: () => api<Kind[]>("/api/provider-kinds") });
  const [adding, setAdding] = useState(false);
  const [keyFor, setKeyFor] = useState<Provider | null>(null);
  const [modelFor, setModelFor] = useState<{ p: Provider; m: Model | null } | null>(null);
  const [deleting, setDeleting] = useState<Provider | null>(null);
  const manage = can("providers.manage");

  const act = useMutation({
    mutationFn: async ({ p, a }: { p: Provider; a: "test" | "discover" | "toggle" | "delete" }) => {
      if (a === "test") return api<{ status: string; error: string | null }>(`/api/providers/${p.id}/test`, { method: "POST" });
      if (a === "discover") return api<{ discovered: number; added: number }>(`/api/providers/${p.id}/discover-models`, { method: "POST" });
      if (a === "toggle") return api(`/api/providers/${p.id}`, { method: "PATCH", body: JSON.stringify({ enabled: !p.enabled }) });
      return api(`/api/providers/${p.id}`, { method: "DELETE" });
    },
    onSuccess: (r, { a }) => {
      qc.invalidateQueries({ queryKey: ["providers"] });
      if (a === "test") {
        const res = r as { status: string; error: string | null };
        toast(res.status === "ok" ? "success" : "error", res.status === "ok" ? t("testOk") : t("testFail", { error: res.error ?? "" }));
      } else if (a === "discover") {
        const res = r as { discovered: number; added: number };
        toast("success", t("discovered", { n: res.discovered, added: res.added }));
      } else toast("success", tc("saved"));
      setDeleting(null);
    },
    onError: (e) => { toast("error", errMsg(e)); setDeleting(null); },
  });

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{t("title")}</h1>
          <p className="text-sm text-muted-foreground">{t("subtitle")}</p>
        </div>
        {manage && <Button onClick={() => setAdding(true)} data-testid="add-provider"><Plus aria-hidden />{t("add")}</Button>}
      </div>
      <p className="flex items-start gap-2 rounded-md border bg-card p-3 text-sm text-muted-foreground">
        <KeyRound className="mt-0.5 size-4 shrink-0" aria-hidden />{t("subscriptionNote")}
      </p>

      {providers.isLoading && <Skeleton className="h-48" />}
      {providers.data?.map((p) => {
        const Icon = STATUS_ICON[p.status as keyof typeof STATUS_ICON] ?? CircleDashed;
        return (
          <Card key={p.id} data-testid={`provider-${p.id}`}>
            <CardHeader className="flex-row flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <CardTitle className="flex flex-wrap items-center gap-2">
                  <Plug className="size-4 text-primary" aria-hidden />
                  <bdi>{p.name}</bdi>
                  {p.is_mock && <span className="inline-flex items-center gap-1 rounded-full bg-warning/10 px-2 py-0.5 text-xs text-foreground"><FlaskConical className="size-3 text-warning" aria-hidden />{t("mockBadge")}</span>}
                </CardTitle>
                <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                  <span>{t.has(`kinds.${p.kind}`) ? t(`kinds.${p.kind}`) : p.kind}</span>
                  <span className={cn("inline-flex items-center gap-1", STATUS_TONE[p.status as keyof typeof STATUS_TONE])}>
                    <Icon className="size-3.5" aria-hidden />{t.has(`status.${p.status}`) ? t(`status.${p.status}`) : p.status}
                  </span>
                  {!p.is_mock && <span>{t("key")}: {p.has_credential ? t("keySet", { hint: p.credential_hint ?? "" }) : t("keyMissing")}</span>}
                  {p.last_tested_at && <span>{t("lastTested", { time: new Date(p.last_tested_at).toLocaleString(nl) })}</span>}
                  {!p.enabled && <span>{t("disable")}</span>}
                </p>
                {p.last_error && <p className="mt-1 break-all text-xs text-destructive" dir="ltr">{p.last_error}</p>}
              </div>
              {manage && (
                <div className="flex flex-wrap gap-1">
                  <Button variant="outline" size="sm" disabled={act.isPending} onClick={() => act.mutate({ p, a: "test" })}>{t("test")}</Button>
                  <Button variant="outline" size="sm" disabled={act.isPending} onClick={() => act.mutate({ p, a: "discover" })}><Search aria-hidden />{t("discover")}</Button>
                  {!p.is_mock && can("credentials.manage") && <Button variant="ghost" size="icon" aria-label={`${t("setKey")}: ${p.name}`} title={t("setKey")} onClick={() => setKeyFor(p)}><KeyRound aria-hidden /></Button>}
                  <Button variant="ghost" size="icon" aria-label={`${p.enabled ? t("disable") : t("enable")}: ${p.name}`} title={p.enabled ? t("disable") : t("enable")} onClick={() => act.mutate({ p, a: "toggle" })}>
                    {p.enabled ? <PowerOff aria-hidden /> : <Power aria-hidden />}
                  </Button>
                  {!p.is_mock && <Button variant="ghost" size="icon" aria-label={`${t("delete")}: ${p.name}`} title={t("delete")} onClick={() => setDeleting(p)}><Trash2 aria-hidden /></Button>}
                </div>
              )}
            </CardHeader>
            <CardContent>
              <div className="mb-2 flex items-center justify-between">
                <h3 className="text-sm font-semibold">{tm("title")}</h3>
                {manage && <Button variant="ghost" size="sm" onClick={() => setModelFor({ p, m: null })}><Plus aria-hidden />{tm("add")}</Button>}
              </div>
              {p.models.length === 0 ? <p className="text-sm text-muted-foreground">{tm("empty")}</p> : (
                <div className="overflow-x-auto" tabIndex={0} aria-label={`${tm("title")}: ${p.name}`}>
                  <table className="w-full min-w-[560px] text-sm">
                    <thead className="text-xs text-muted-foreground">
                      <tr>{(["name", "context", "input", "output", "cached"] as const).map((c) => <th key={c} scope="col" className="py-2 text-start font-medium">{tm(c)}</th>)}<th scope="col" className="py-2"><span className="sr-only">{tc("actions")}</span></th></tr>
                    </thead>
                    <tbody className="divide-y">
                      {p.models.map((m) => (
                        <tr key={m.id} className={cn(!m.enabled && "text-muted-foreground")}>
                          <td className="py-2 font-mono text-xs" dir="ltr">{m.name}</td>
                          <td className="py-2 tabular-nums">{m.context_window ? new Intl.NumberFormat(nl).format(m.context_window) : "—"}</td>
                          {[m.input_price_per_mtok, m.output_price_per_mtok, m.cached_input_price_per_mtok].map((v, i) => (
                            <td key={i} className="py-2 tabular-nums" dir="ltr">{v !== null ? `$${Number(v)}` : <span className="text-muted-foreground">{tm("noPrice")}</span>}</td>
                          ))}
                          <td className="py-2 text-end">
                            {manage && <Button variant="ghost" size="icon" aria-label={`${tm("edit")}: ${m.name}`} onClick={() => setModelFor({ p, m })}><Pencil aria-hidden /></Button>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        );
      })}

      {adding && kinds.data && <AddProviderDialog open onOpenChange={setAdding} kinds={kinds.data} />}
      {keyFor && <KeyDialog provider={keyFor} onClose={() => setKeyFor(null)} />}
      {modelFor && <ModelDialog provider={modelFor.p} model={modelFor.m} onClose={() => setModelFor(null)} />}
      {deleting && (
        <ConfirmDialog open onOpenChange={(o) => !o && setDeleting(null)} title={t("deleteTitle")} description={t("deleteBody")}
          confirmLabel={t("delete")} destructive busy={act.isPending} onConfirm={() => act.mutate({ p: deleting, a: "delete" })} />
      )}
    </div>
  );
}
