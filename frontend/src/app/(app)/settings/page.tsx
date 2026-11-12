"use client";
import { UserPlus } from "lucide-react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { LanguageSwitcher } from "@/components/language-switcher";
import { useCan, useUser } from "@/components/providers";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/select";
import { useAdminActions, useRoles, useUsers } from "@/lib/admin";
import { ApiError } from "@/lib/api";
import { setShowAdvanced, useShowAdvanced } from "@/lib/nav-mode";
import { useToasts } from "@/lib/toast";

export default function SettingsPage() {
  const t = useTranslations("settings");
  const can = useCan();
  const me = useUser();
  const locale = useLocale();
  const roleLabel = me ? ((locale === "ar" ? me.role_name_ar : me.role_name_en) ?? me.role) : "—";
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="max-w-3xl text-sm text-muted-foreground">{t("subtitle")}</p>
      </div>
      <Card className="flex flex-col gap-4 p-4" aria-labelledby="pref-h">
        <h2 id="pref-h" className="text-lg font-semibold">{t("preferences")}</h2>
        <dl className="grid gap-3 text-sm sm:grid-cols-2">
          <div><dt className="text-xs text-muted-foreground">{t("signedInAs")}</dt><dd dir="ltr" className="text-start">{me?.username}</dd></div>
          <div><dt className="text-xs text-muted-foreground">{t("role")}</dt><dd>{roleLabel}</dd></div>
        </dl>
        <div className="flex flex-wrap items-center gap-4">
          <div className="flex items-center gap-2 text-sm"><span>{t("language")}</span><LanguageSwitcher signedIn /></div>
          <div className="flex items-center gap-2 text-sm"><span>{t("theme")}</span><ThemeToggle /></div>
        </div>
      </Card>
      <AdvancedTools />
      {can("users.manage") && <Users />}
      <Card className="flex flex-col gap-2 p-4" aria-labelledby="more-h">
        <h2 id="more-h" className="text-lg font-semibold">{t("more")}</h2>
        <ul className="grid gap-2 text-sm sm:grid-cols-2">
          {can("browser_bridge.manage") && <li><Link className="text-primary underline" href="/browser-bridge">{t("links.bridge")}</Link></li>}
          {can("budgets.manage") && <li><Link className="text-primary underline" href="/analytics">{t("links.budgets")}</Link></li>}
          {can("security.manage") && <li><Link className="text-primary underline" href="/integrations">{t("links.keys")}</Link></li>}
          <li><Link className="text-primary underline" href="/templates">{t("links.templates")}</Link></li>
          {can("supervisor.read") && <li><Link className="text-primary underline" href="/supervisor">{t("links.supervisor")}</Link></li>}
        </ul>
      </Card>
    </div>
  );
}

function Users() {
  const t = useTranslations("settings");
  const tc = useTranslations("common");
  const locale = useLocale();
  const users = useUsers(true);
  const roles = useRoles(true);
  const a = useAdminActions();
  const toast = useToasts((s) => s.push);
  const fail = (e: unknown) => {
    const code = e instanceof ApiError ? e.code : "error";
    toast("error", t.has(`errors.${code}`) ? t(`errors.${code}`) : tc("requestFailed", { code }));
  };
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("DEVELOPER");
  const roleName = (k: string) => { const r = roles.data?.find((x) => x.key === k); return r ? (locale === "ar" ? r.name_ar : r.name_en) : k; };
  return (
    <Card className="flex flex-col gap-4 p-4" aria-labelledby="users-h">
      <h2 id="users-h" className="text-lg font-semibold">{t("users")}</h2>
      <form className="grid gap-3 sm:grid-cols-4 sm:items-end" onSubmit={(e) => {
        e.preventDefault();
        a.createUser.mutate({ username: username.trim(), password, role, language: locale }, { onSuccess: () => { setUsername(""); setPassword(""); toast("success", tc("saved")); }, onError: fail });
      }}>
        <div className="flex flex-col gap-1.5"><Label htmlFor="u-name">{t("username")}</Label>
          <Input id="u-name" required minLength={3} maxLength={64} pattern="[A-Za-z0-9_.\-]+" autoComplete="off" value={username} onChange={(e) => setUsername(e.target.value)} dir="ltr" /></div>
        <div className="flex flex-col gap-1.5"><Label htmlFor="u-pass">{t("password")}</Label>
          <Input id="u-pass" required type="password" minLength={12} autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} dir="ltr" aria-describedby="u-pass-h" /></div>
        <div className="flex flex-col gap-1.5"><Label htmlFor="u-role">{t("role")}</Label>
          <NativeSelect id="u-role" value={role} onChange={(e) => setRole(e.target.value)}>
            {roles.data?.map((r) => <option key={r.key} value={r.key}>{roleName(r.key)}</option>)}
          </NativeSelect></div>
        <Button type="submit" disabled={a.createUser.isPending} data-testid="user-add"><UserPlus aria-hidden />{t("addUser")}</Button>
        <p id="u-pass-h" className="text-xs text-muted-foreground sm:col-span-4">{t("passwordHelp")}</p>
      </form>
      <div className="overflow-x-auto" tabIndex={0} aria-label={t("users")}>
        <table className="w-full min-w-[520px] text-sm" data-testid="user-table">
          <thead className="border-b text-xs text-muted-foreground"><tr>
            <th scope="col" className="px-3 py-2 text-start font-medium">{t("username")}</th>
            <th scope="col" className="px-3 py-2 text-start font-medium">{t("role")}</th>
            <th scope="col" className="px-3 py-2 text-start font-medium">{t("status")}</th>
          </tr></thead>
          <tbody className="divide-y">
            {users.data?.map((u) => (
              <tr key={u.id}>
                <td className="px-3 py-2" dir="ltr">{u.username}</td>
                <td className="px-3 py-2">
                  <NativeSelect aria-label={`${t("role")}: ${u.username}`} value={u.role} onChange={(e) => a.patchUser.mutate({ id: u.id, role: e.target.value }, { onError: fail })}>
                    {roles.data?.map((r) => <option key={r.key} value={r.key}>{roleName(r.key)}</option>)}
                  </NativeSelect>
                </td>
                <td className="px-3 py-2">
                  <label className="flex items-center gap-2">
                    <input type="checkbox" className="size-4" checked={u.is_active} onChange={(e) => a.patchUser.mutate({ id: u.id, is_active: e.target.checked }, { onError: fail })} />
                    <span>{u.is_active ? t("active") : t("inactive")}</span>
                  </label>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}


/** ADR-036: the older general-purpose pages are hidden from the menu by default; this brings them back. Nothing is
 * removed either way. */
function AdvancedTools() {
  const t = useTranslations("settings.advanced");
  const on = useShowAdvanced();
  return (
    <Card className="flex flex-col gap-3 p-4" aria-labelledby="nav-advanced-h" id="nav-advanced">
      <h2 id="nav-advanced-h" className="text-lg font-semibold">{t("title")}</h2>
      <p className="max-w-3xl text-sm text-muted-foreground">{t("help")}</p>
      <label className="flex min-h-11 cursor-pointer items-center gap-3 text-sm font-medium">
        <input type="checkbox" role="switch" aria-checked={on} checked={on} onChange={(e) => setShowAdvanced(e.target.checked)}
          className="size-5 accent-[hsl(var(--primary))]" data-testid="settings-show-advanced" />
        {t("toggle")}
      </label>
      <p className="text-xs text-muted-foreground" aria-live="polite">{on ? t("onNote") : t("offNote")}</p>
    </Card>
  );
}
