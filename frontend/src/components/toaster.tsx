"use client";
import { AlertCircle, CheckCircle2, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { useToasts } from "@/lib/toast";
import { cn } from "@/lib/utils";

export function Toaster() {
  const t = useTranslations("common");
  const { toasts, dismiss } = useToasts();
  return (
    <div className="pointer-events-none fixed bottom-4 end-4 z-[60] flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2" aria-live="polite" role="status">
      {toasts.map((x) => (
        <div key={x.id} className={cn("pointer-events-auto flex items-start gap-2 rounded-md border bg-card p-3 text-sm shadow-lg",
          x.kind === "error" && "border-destructive/40")}>
          {x.kind === "success"
            ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" aria-hidden />
            : <AlertCircle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />}
          <span className="flex-1">{x.text}</span>
          <button className="rounded p-0.5 text-muted-foreground hover:bg-muted" aria-label={t("close")} onClick={() => dismiss(x.id)}>
            <X className="size-3.5" aria-hidden />
          </button>
        </div>
      ))}
    </div>
  );
}
