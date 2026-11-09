"use client";
import { AlertCircle, Loader2, LogIn } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";
import { setLocaleCookie } from "@/app/actions";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError, type User } from "@/lib/api";

const KNOWN = ["invalid_credentials", "too_many_attempts", "network"] as const;

export function LoginForm() {
  const t = useTranslations("auth");
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState<{ username?: boolean; password?: boolean }>({});
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    const username = String(fd.get("username") ?? "").trim();
    const password = String(fd.get("password") ?? "");
    const m = { username: !username, password: !password };
    setMissing(m);
    setError(null);
    if (m.username || m.password) return;
    setBusy(true);
    try {
      const user = await api<User>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      await setLocaleCookie(user.language); // restore the user's saved language
      window.location.assign("/");
    } catch (err) {
      const code = err instanceof ApiError ? err.code : "network";
      setError((KNOWN as readonly string[]).includes(code) ? code : "network");
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <span>{t("title")}</span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
          {error && (
            <div role="alert" className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
              <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
              <span>{t(`errors.${error}`)}</span>
            </div>
          )}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="username">{t("username")}</Label>
            <Input id="username" name="username" autoComplete="username" dir="ltr" aria-invalid={missing.username || undefined}
              aria-describedby={missing.username ? "username-err" : undefined} />
            {missing.username && <p id="username-err" className="text-sm text-destructive">{t("errors.required")}</p>}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="password">{t("password")}</Label>
            <Input id="password" name="password" type="password" autoComplete="current-password" dir="ltr"
              aria-invalid={missing.password || undefined} aria-describedby={missing.password ? "password-err" : undefined} />
            {missing.password && <p id="password-err" className="text-sm text-destructive">{t("errors.required")}</p>}
          </div>
          <Button type="submit" disabled={busy} className="mt-2 w-full">
            {busy ? <Loader2 className="animate-spin" aria-hidden /> : <LogIn className="rtl:-scale-x-100" aria-hidden />}
            {busy ? t("submitting") : t("submit")}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
