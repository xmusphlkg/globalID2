"use client";

import type { ReactNode } from "react";
import { Dialog, DialogBackdrop, DialogPanel, DialogTitle } from "@headlessui/react";
import { useAppStore } from "@/stores/app-store";
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
  const lang = useAppStore(state => state.lang);

  return (
    <Dialog open={open} onClose={onClose} className="relative z-50">
      <DialogBackdrop className="fixed inset-0 bg-tremor-background-emphasis/45" />
      <div className="fixed inset-0 flex justify-end">
        <DialogPanel
          className={cn(
          "relative flex h-full w-full max-w-3xl flex-col border-l border-tremor-border bg-tremor-background shadow-2xl dark:border-dark-tremor-border dark:bg-dark-tremor-background sm:w-[720px]",
          className,
        )}
      >
        <div className="flex items-start justify-between gap-4 border-b border-tremor-border bg-tremor-background-subtle/70 px-5 py-4 dark:border-dark-tremor-border">
          <div className="min-w-0">
            <DialogTitle className="truncate text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {title}
            </DialogTitle>
            {subtitle ? (
              <p className="mt-1 truncate text-sm text-tremor-content dark:text-dark-tremor-content">
                {subtitle}
              </p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-tremor-default border border-tremor-border text-tremor-content-subtle transition hover:bg-tremor-background-subtle hover:text-tremor-content-strong dark:border-dark-tremor-border dark:text-dark-tremor-content-subtle dark:hover:bg-dark-tremor-background-subtle dark:hover:text-dark-tremor-content-strong"
            aria-label={lang === "zh" ? "关闭" : "Close"}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </DialogPanel>
      </div>
    </Dialog>
  );
}
