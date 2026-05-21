import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

type MetricTone = "neutral" | "primary" | "success" | "warning" | "danger" | "info";

const toneClasses: Record<MetricTone, string> = {
  neutral: "bg-slate-50 text-slate-700 dark:bg-slate-900/30 dark:text-slate-300",
  primary: "bg-teal-50 text-teal-700 dark:bg-teal-950/30 dark:text-teal-300",
  success: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300",
  warning: "bg-amber-50 text-amber-700 dark:bg-amber-950/30 dark:text-amber-300",
  danger: "bg-rose-50 text-rose-700 dark:bg-rose-950/30 dark:text-rose-300",
  info: "bg-sky-50 text-sky-700 dark:bg-sky-950/30 dark:text-sky-300",
};

interface MetricTileProps {
  label: ReactNode;
  value: ReactNode;
  icon?: ReactNode;
  hint?: ReactNode;
  tone?: MetricTone;
  className?: string;
}

export function MetricTile({
  label,
  value,
  icon,
  hint,
  tone = "neutral",
  className,
}: MetricTileProps) {
  return (
    <div
      className={cn(
        "min-h-[104px] rounded-tremor-default border border-tremor-border bg-tremor-background px-4 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background",
        className,
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {label}
          </p>
          <p className="mt-2 truncate text-2xl font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {value}
          </p>
        </div>
        {icon ? (
          <div className={cn("flex h-9 w-9 shrink-0 items-center justify-center rounded-tremor-default", toneClasses[tone])}>
            {icon}
          </div>
        ) : null}
      </div>
      {hint ? (
        <p className="mt-2 truncate text-xs text-tremor-content dark:text-dark-tremor-content">{hint}</p>
      ) : null}
    </div>
  );
}
