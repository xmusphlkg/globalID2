
import { type ReactNode } from "react";

import { StatusBadge } from "@/components/ui/StatusBadge";

import { type AutomationJob } from "@/features/operations/automation/api";

import { cn } from "@/lib/utils";

export const inputClass =
  "h-10 w-full rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted disabled:bg-tremor-background-subtle disabled:text-tremor-content-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong";

export function FormSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3 border-b border-tremor-border pb-5 last:border-b-0 last:pb-0 dark:border-dark-tremor-border">
      <h3 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
        {title}
      </h3>
      {children}
    </section>
  );
}

export function Field({
  label,
  children,
  hint,
  className,
}: {
  label: string;
  children: ReactNode;
  hint?: ReactNode;
  className?: string;
}) {
  return (
    <label className={cn("block space-y-1.5", className)}>
      <span className="text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {label}
      </span>
      {children}
      {hint ? <span className="block text-xs text-tremor-content dark:text-dark-tremor-content">{hint}</span> : null}
    </label>
  );
}

export function ActionButton({
  children,
  icon,
  tone = "neutral",
  disabled,
  onClick,
  type = "button",
  className,
}: {
  children: ReactNode;
  icon?: ReactNode;
  tone?: "neutral" | "primary" | "danger";
  disabled?: boolean;
  onClick?: (event: React.MouseEvent<HTMLButtonElement>) => void;
  type?: "button" | "submit";
  className?: string;
}) {
  const toneClass =
    tone === "primary"
      ? "border-tremor-brand bg-tremor-brand text-tremor-brand-inverted hover:bg-tremor-brand/90"
      : tone === "danger"
        ? "border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-300"
        : "border-tremor-border bg-tremor-background text-tremor-content-strong hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle";

  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "inline-flex h-9 items-center justify-center gap-2 rounded-tremor-default border px-3 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-55",
        toneClass,
        className,
      )}
    >
      {icon}
      {children}
    </button>
  );
}

export function InlineStatus({
  icon,
  label,
  value,
}: {
  icon: ReactNode;
  label: string;
  value: ReactNode;
}) {
  return (
    <div className="flex items-start gap-3 border-b border-tremor-border py-3 last:border-b-0 dark:border-dark-tremor-border">
      <div className="mt-0.5 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{icon}</div>
      <div className="min-w-0">
        <p className="text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{label}</p>
        <div className="mt-1 text-sm text-tremor-content-strong dark:text-dark-tremor-content-strong">{value}</div>
      </div>
    </div>
  );
}

export function AlertPanel({ tone, children }: { tone: "warning" | "danger" | "info"; children: ReactNode }) {
  const toneClass =
    tone === "danger"
      ? "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900/50 dark:bg-rose-950/25 dark:text-rose-200"
      : tone === "warning"
        ? "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/25 dark:text-amber-200"
        : "border-sky-200 bg-sky-50 text-sky-800 dark:border-sky-900/50 dark:bg-sky-950/25 dark:text-sky-200";

  return (
    <div className={cn("rounded-tremor-default border px-4 py-3 text-sm", toneClass)}>
      {children}
    </div>
  );
}

export function JobHealthBadge({ job }: { job: AutomationJob }) {
  if (!job.enabled) return <StatusBadge status="disabled">disabled</StatusBadge>;
  if (job.last_status === "failed") return <StatusBadge status="failed">failed</StatusBadge>;
  return <StatusBadge status="enabled">enabled</StatusBadge>;
}
