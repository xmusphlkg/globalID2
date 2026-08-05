import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import type { Color } from "@/components/ui/tremor";

export const inputClass =
  "h-10 w-full rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong";

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
  onClick?: () => void;
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

export function Panel({ children, className }: { children: ReactNode; className?: string }) {
  return <section className={cn("app-panel p-4", className)}>{children}</section>;
}

export function formatDateTime(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export function fieldValue(value: unknown): string {
  if (typeof value === "string") return value.trim() || "—";
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  return "—";
}

export function statusColor(status: string): Color {
  switch (status) {
    case "published": return "emerald";
    case "requires_review": return "amber";
    case "draft": return "blue";
    case "blocked": return "slate";
    default: return "slate";
  }
}

export function briefStatusColor(status: string): Color {
  switch (status) {
    case "published": return "emerald";
    case "requires_review": return "amber";
    case "draft": return "blue";
    default: return "slate";
  }
}

export function DetailSkeleton() {
  return (
    <div className="space-y-4">
      {[1, 2, 3].map((index) => (
        <div
          key={index}
          className="h-24 animate-pulse rounded-tremor-default border border-tremor-border bg-tremor-background-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background-muted"
        />
      ))}
    </div>
  );
}
