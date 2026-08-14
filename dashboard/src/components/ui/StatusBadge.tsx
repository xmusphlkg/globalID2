import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

type StatusTone = "neutral" | "info" | "success" | "warning" | "danger" | "primary";

const toneClasses: Record<StatusTone, string> = {
  neutral: "border-stone-200 bg-stone-50 text-stone-700 dark:border-slate-800 dark:bg-slate-950/40 dark:text-slate-300",
  info: "border-blue-200 bg-blue-50 text-blue-700 dark:border-sky-900/50 dark:bg-sky-950/30 dark:text-sky-300",
  success: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/50 dark:bg-emerald-950/30 dark:text-emerald-300",
  warning: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300",
  danger: "border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900/50 dark:bg-rose-950/30 dark:text-rose-300",
  primary: "border-teal-200 bg-teal-50 text-teal-700 dark:border-teal-900/50 dark:bg-teal-950/30 dark:text-teal-300",
};

const statusToneMap: Record<string, StatusTone> = {
  completed: "success",
  published: "success",
  approved: "success",
  ready: "success",
  running: "warning",
  retrying: "warning",
  queued: "info",
  generating: "info",
  reviewing: "info",
  pending: "neutral",
  no_model: "warning",
  not_required: "success",
  skipped: "neutral",
  cancelled: "neutral",
  failed: "danger",
  error: "danger",
  stopped: "danger",
  disabled: "warning",
  enabled: "success",
};

interface StatusBadgeProps {
  children: ReactNode;
  status?: string | null;
  tone?: StatusTone;
  className?: string;
}

export function StatusBadge({ children, status, tone, className }: StatusBadgeProps) {
  const resolvedTone = tone ?? (status ? statusToneMap[status.toLowerCase()] : undefined) ?? "neutral";

  return (
    <span
      className={cn(
        "inline-flex h-6 max-w-full items-center rounded-tremor-default border px-2 text-xs font-semibold",
        toneClasses[resolvedTone],
        className,
      )}
    >
      <span className="truncate">{children}</span>
    </span>
  );
}
