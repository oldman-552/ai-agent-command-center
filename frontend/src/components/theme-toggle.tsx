"use client";
import { Monitor, Moon, Sun } from "lucide-react";
import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";

type Theme = "light" | "dark" | "system";
const order: Theme[] = ["light", "dark", "system"];

function apply(theme: Theme) {
  const dark = theme === "dark" || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

export function ThemeToggle() {
  const t = useTranslations("theme");
  const [theme, setTheme] = useState<Theme>("system");

  useEffect(() => {
    try {
      const saved = localStorage.getItem("theme") as Theme | null;
      if (saved && order.includes(saved)) setTheme(saved);
    } catch {
      /* storage unavailable */
    }
  }, []);

  const Icon = theme === "light" ? Sun : theme === "dark" ? Moon : Monitor;
  const next = order[(order.indexOf(theme) + 1) % order.length];

  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={`${t("label")}: ${t(theme)}`}
      title={`${t("label")}: ${t(theme)}`}
      onClick={() => {
        setTheme(next);
        apply(next);
        try {
          localStorage.setItem("theme", next);
        } catch {
          /* storage unavailable */
        }
      }}
    >
      <Icon aria-hidden />
    </Button>
  );
}
