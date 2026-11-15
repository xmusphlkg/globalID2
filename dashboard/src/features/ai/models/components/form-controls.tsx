
import { type MouseEvent, type ReactNode } from "react";

import { cn } from "@/lib/utils";

import { formatDateTime } from "../presentation";
export const inputClass =
  "h-10 w-full rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted disabled:bg-tremor-background-subtle disabled:text-tremor-content-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong";

export function ActionButton({
  children,
  icon,
  tone = "neutral",
  disabled,
  onClick,
  type = "button",
  className,
  title,
  ariaLabel,
}: {
  children: ReactNode;
  icon?: ReactNode;
  tone?: "neutral" | "primary" | "danger";
  disabled?: boolean;
  onClick?: (event: MouseEvent<HTMLButtonElement>) => void;
  type?: "button" | "submit";
  className?: string;
  title?: string;
  ariaLabel?: string;
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
      title={title}
      aria-label={ariaLabel}
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

export function IconActionButton({
  icon,
  label,
  tone = "neutral",
  disabled,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  tone?: "neutral" | "danger";
  disabled?: boolean;
  onClick?: (event: MouseEvent<HTMLButtonElement>) => void;
}) {
  return (
    <ActionButton
      tone={tone}
      className="h-8 w-8 px-0"
      disabled={disabled}
      onClick={onClick}
      icon={icon}
      title={label}
      ariaLabel={label}
    >
      <span className="sr-only">{label}</span>
    </ActionButton>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">{label}</span>
      {children}
      {hint ? <span className="block text-xs text-tremor-content dark:text-dark-tremor-content">{hint}</span> : null}
    </label>
  );
}

export function FormSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-3 border-b border-tremor-border pb-5 last:border-b-0 last:pb-0 dark:border-dark-tremor-border">
      <h3 className="text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">{title}</h3>
      {children}
    </section>
  );
}

export function AlertBox({ tone, children }: { tone: "danger" | "warning"; children: ReactNode }) {
  const toneClass =
    tone === "danger"
      ? "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900/50 dark:bg-rose-950/25 dark:text-rose-200"
      : "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900/50 dark:bg-amber-950/25 dark:text-amber-200";
  return <div className={cn("rounded-tremor-default border px-4 py-3 text-sm", toneClass)}>{children}</div>;
}

export function LastCheckCell({ checkedAt, message, compact = false }: { checkedAt?: string | null; message?: string | null; compact?: boolean }) {
  return (
    <div className={compact ? "min-w-[180px] max-w-[280px]" : "min-w-[220px] max-w-[360px]"}>
      <p className="whitespace-nowrap text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {formatDateTime(checkedAt)}
      </p>
      <p
        className="mt-1 truncate text-sm text-tremor-content-strong dark:text-dark-tremor-content-strong"
        title={message || undefined}
      >
        {message || "-"}
      </p>
    </div>
  );
}

export function SkeletonRows() {
  return (
    <div className="space-y-3">
      {[1, 2, 3].map((item) => (
        <div key={item} className="h-16 animate-pulse rounded-tremor-default border border-tremor-border bg-tremor-background dark:border-dark-tremor-border dark:bg-dark-tremor-background" />
      ))}
    </div>
  );
}
