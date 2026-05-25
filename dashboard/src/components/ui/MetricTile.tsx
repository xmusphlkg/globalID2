import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

type MetricTone = "neutral" | "primary" | "success" | "warning" | "danger" | "info";

const toneClasses: Record<MetricTone, string> = {
  neutral: "bg-stone-100 text-stone-700 dark:bg-slate-900/30 dark:text-slate-300",
  primary: "bg-teal-50 text-teal-700 dark:bg-teal-950/30 dark:text-teal-300",
  success: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300",
  warning: "bg-amber-50 text-amber-700 dark:bg-amber-950/30 dark:text-amber-300",
  danger: "bg-rose-50 text-rose-700 dark:bg-rose-950/30 dark:text-rose-300",
  info: "bg-blue-50 text-blue-700 dark:bg-sky-950/30 dark:text-sky-300",
};

const accentClasses: Record<MetricTone, string> = {
  neutral: "bg-stone-300",
  primary: "bg-teal-500",
  success: "bg-emerald-500",
  warning: "bg-amber-500",
  danger: "bg-rose-500",
  info: "bg-blue-500",
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
        "group relative min-h-[112px] overflow-hidden rounded-tremor-default border border-tremor-border bg-tremor-background px-4 py-3 shadow-[0_1px_2px_rgba(23,33,31,0.04)] transition hover:-translate-y-0.5 hover:shadow-[0_8px_18px_rgba(23,33,31,0.08)] dark:border-dark-tremor-border dark:bg-dark-tremor-background",
        className,
      )}
    >
      <div className={cn("absolute inset-x-0 top-0 h-0.5", accentClasses[tone])} />
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {label}
          </p>
          <p className="mt-2 truncate text-[1.7rem] font-semibold leading-8 text-tremor-content-strong dark:text-dark-tremor-content-strong">
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
