import { Bot } from "lucide-react";
import { getTranslations } from "next-intl/server";
import { redirect } from "next/navigation";
import { LanguageSwitcher } from "@/components/language-switcher";
import { ThemeToggle } from "@/components/theme-toggle";
import { getSessionUser } from "@/lib/server-api";
import { LoginForm } from "./login-form";

export default async function LoginPage() {
  if (await getSessionUser()) redirect("/");
  const t = await getTranslations();
  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex items-center justify-end gap-2 p-4">
        <ThemeToggle />
        <LanguageSwitcher />
      </header>
      <main id="main" className="flex flex-1 items-center justify-center px-4 pb-16">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex flex-col items-center gap-3 text-center">
            <span className="flex size-12 items-center justify-center rounded-xl bg-primary text-primary-foreground">
              <Bot className="size-6" aria-hidden />
            </span>
            <h1 className="text-2xl font-bold">{t("app.fullName")}</h1>
            <p className="text-sm text-muted-foreground">{t("auth.subtitle")}</p>
          </div>
          <LoginForm />
        </div>
      </main>
    </div>
  );
}
