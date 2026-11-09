"use client";
import { Dialog as D } from "radix-ui";
import { X } from "lucide-react";
import * as React from "react";
import { cn } from "@/lib/utils";

export const Dialog = D.Root;
export const DialogTrigger = D.Trigger;
export const DialogClose = D.Close;

export function DialogContent({ className, children, closeLabel, ...p }: React.ComponentProps<typeof D.Content> & { closeLabel: string }) {
  return (
    <D.Portal>
      <D.Overlay className="fixed inset-0 z-50 bg-black/50" />
      <D.Content
        className={cn(
          "fixed start-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-lg -translate-y-1/2 ltr:-translate-x-1/2 rtl:translate-x-1/2 rounded-lg border bg-card p-6 shadow-xl",
          className,
        )}
        {...p}
      >
        {children}
        <D.Close className="absolute end-3 top-3 rounded-md p-1 text-muted-foreground hover:bg-muted" aria-label={closeLabel}>
          <X className="size-4" aria-hidden />
        </D.Close>
      </D.Content>
    </D.Portal>
  );
}
export const DialogTitle = ({ className, ...p }: React.ComponentProps<typeof D.Title>) => (
  <D.Title className={cn("text-lg font-semibold", className)} {...p} />
);
export const DialogDescription = ({ className, ...p }: React.ComponentProps<typeof D.Description>) => (
  <D.Description className={cn("mt-2 text-sm text-muted-foreground", className)} {...p} />
);
