"use client";

import { useEffect, type ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

interface DetailDrawerProps {
  open: boolean;
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  onClose: () => void;
  className?: string;
}

export function DetailDrawer({
  open,
  title,
  subtitle,
  children,
  onClose,
  className,
}: DetailDrawerProps) {
  useEffect(() => {
    if (!open) return;

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };

    document.addEventListener("keydown", handleKeyDown);
    document.body.style.overflow = "hidden";

    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "";
    };
  }, [onClose, open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50">
      <button
        type="button"
        aria-label="Close detail drawer"
        className="absolute inset-0 h-full w-full bg-slate-950/35"
        onClick={onClose}
      />
      <aside
        className={cn(
          "absolute right-0 top-0 flex h-full w-full max-w-3xl flex-col border-l border-tremor-border bg-tremor-background shadow-xl dark:border-dark-tremor-border dark:bg-dark-tremor-background sm:w-[720px]",
          className,
        )}
      >
        <div className="flex items-start justify-between gap-4 border-b border-tremor-border px-5 py-4 dark:border-dark-tremor-border">
          <div className="min-w-0">
            <h2 className="truncate text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {title}
            </h2>
            {subtitle ? (
              <p className="mt-1 truncate text-sm text-tremor-content dark:text-dark-tremor-content">
                {subtitle}
              </p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-tremor-default border border-tremor-border text-tremor-content-subtle transition hover:bg-tremor-background-subtle hover:text-tremor-content-strong dark:border-dark-tremor-border dark:text-dark-tremor-content-subtle dark:hover:bg-dark-tremor-background-subtle dark:hover:text-dark-tremor-content-strong"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </aside>
    </div>
  );
}
