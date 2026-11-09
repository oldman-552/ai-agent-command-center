"use client";
import { Languages } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useTransition } from "react";
import { setLocaleCookie } from "@/app/actions";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { Locale } from "@/i18n/config";

/** Toggles ar <-> en. Persists in a cookie and, when signed in, on the user profile. */
export function LanguageSwitcher({ signedIn = false }: { signedIn?: boolean }) {
  const t = useTranslations("language");
  const locale = useLocale() as Locale;
  const next: Locale = locale === "ar" ? "en" : "ar";
  const router = useRouter();
  const [pending, start] = useTransition();

  return (
    <Button
      variant="outline"
      size="sm"
      disabled={pending}
      aria-label={t("switchTo", { language: t(next) })}
      data-testid="language-switcher"
      onClick={() =>
        start(async () => {
          await setLocaleCookie(next);
          if (signedIn) {
            await api("/api/auth/me/language", { method: "PUT", body: JSON.stringify({ language: next }) }).catch(
              () => undefined,
            );
          }
          router.refresh();
        })
      }
    >
      <Languages aria-hidden />
      <span lang={next}>{t(next)}</span>
    </Button>
  );
}
