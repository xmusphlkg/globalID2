import type {
  ButtonHTMLAttributes,
  ComponentType,
  HTMLAttributes,
  ReactNode,
} from "react";

import { cn } from "@/lib/utils";

export type Color =
  | "amber"
  | "blue"
  | "cyan"
  | "emerald"
  | "gray"
  | "indigo"
  | "rose"
  | "sky"
  | "slate"
  | "teal"
  | "violet"
  | "yellow";

const badgeColors: Record<Color, string> = {
  amber: "bg-amber-100 text-amber-800 dark:bg-amber-950/60 dark:text-amber-300",
  blue: "bg-blue-100 text-blue-800 dark:bg-blue-950/60 dark:text-blue-300",
  cyan: "bg-cyan-100 text-cyan-800 dark:bg-cyan-950/60 dark:text-cyan-300",
  emerald: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300",
  gray: "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-300",
  indigo: "bg-indigo-100 text-indigo-800 dark:bg-indigo-950/60 dark:text-indigo-300",
  rose: "bg-rose-100 text-rose-800 dark:bg-rose-950/60 dark:text-rose-300",
  sky: "bg-sky-100 text-sky-800 dark:bg-sky-950/60 dark:text-sky-300",
  slate: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  teal: "bg-teal-100 text-teal-800 dark:bg-teal-950/60 dark:text-teal-300",
  violet: "bg-violet-100 text-violet-800 dark:bg-violet-950/60 dark:text-violet-300",
  yellow: "bg-yellow-100 text-yellow-800 dark:bg-yellow-950/60 dark:text-yellow-300",
};

const progressColors: Record<Color, string> = {
  amber: "bg-amber-500",
  blue: "bg-blue-500",
  cyan: "bg-cyan-500",
  emerald: "bg-emerald-500",
  gray: "bg-gray-500",
  indigo: "bg-indigo-500",
  rose: "bg-rose-500",
  sky: "bg-sky-500",
  slate: "bg-slate-500",
  teal: "bg-teal-500",
  violet: "bg-violet-500",
  yellow: "bg-yellow-500",
};

type BadgeProps = Omit<HTMLAttributes<HTMLSpanElement>, "color"> & {
  color?: Color;
  size?: "xs" | "sm" | "md";
};

export function Badge({ color = "blue", size = "sm", className, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full font-medium",
        size === "xs" ? "px-2 py-0.5 text-[11px]" : size === "md" ? "px-3 py-1 text-sm" : "px-2.5 py-0.5 text-xs",
        badgeColors[color],
        className,
      )}
      {...props}
    />
  );
}

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-tremor-default border border-tremor-border bg-tremor-background p-6 shadow-tremor-card dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:shadow-dark-tremor-card",
        className,
      )}
      {...props}
    />
  );
}

const gridColumns = {
  1: "grid-cols-1",
  2: "grid-cols-2",
  3: "grid-cols-3",
  4: "grid-cols-4",
  5: "grid-cols-5",
  6: "grid-cols-6",
} as const;

const smGridColumns = {
  1: "sm:grid-cols-1",
  2: "sm:grid-cols-2",
  3: "sm:grid-cols-3",
  4: "sm:grid-cols-4",
  5: "sm:grid-cols-5",
  6: "sm:grid-cols-6",
} as const;

const mdGridColumns = {
  1: "md:grid-cols-1",
  2: "md:grid-cols-2",
  3: "md:grid-cols-3",
  4: "md:grid-cols-4",
  5: "md:grid-cols-5",
  6: "md:grid-cols-6",
} as const;

const lgGridColumns = {
  1: "lg:grid-cols-1",
  2: "lg:grid-cols-2",
  3: "lg:grid-cols-3",
  4: "lg:grid-cols-4",
  5: "lg:grid-cols-5",
  6: "lg:grid-cols-6",
} as const;

type GridCount = keyof typeof gridColumns;

type GridProps = HTMLAttributes<HTMLDivElement> & {
  numItems?: GridCount;
  numItemsSm?: GridCount;
  numItemsMd?: GridCount;
  numItemsLg?: GridCount;
};

export function Grid({
  numItems = 1,
  numItemsSm,
  numItemsMd,
  numItemsLg,
  className,
  ...props
}: GridProps) {
  return (
    <div
      className={cn(
        "grid",
        gridColumns[numItems],
        numItemsSm && smGridColumns[numItemsSm],
        numItemsMd && mdGridColumns[numItemsMd],
        numItemsLg && lgGridColumns[numItemsLg],
        className,
      )}
      {...props}
    />
  );
}

export function Text({ className, ...props }: HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("text-sm text-tremor-content dark:text-dark-tremor-content", className)} {...props} />;
}

export function Title({ className, ...props }: HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={cn("text-tremor-title font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong", className)} {...props} />;
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  icon?: ComponentType<{ className?: string }>;
  loading?: boolean;
  variant?: "primary" | "secondary" | "light";
  size?: "xs" | "sm" | "md";
  children?: ReactNode;
};

export function Button({
  icon: Icon,
  loading = false,
  variant = "primary",
  size = "md",
  className,
  children,
  disabled,
  type = "button",
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-tremor-default font-semibold transition disabled:cursor-not-allowed disabled:opacity-50",
        size === "xs" ? "h-7 px-2.5 text-xs" : size === "sm" ? "h-9 px-3 text-sm" : "h-10 px-4 text-sm",
        variant === "primary" && "bg-tremor-brand text-tremor-brand-inverted hover:brightness-95",
        variant === "secondary" && "border border-tremor-border bg-tremor-background text-tremor-content-strong hover:bg-tremor-background-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-muted",
        variant === "light" && "text-tremor-brand hover:bg-tremor-brand-muted/60 dark:text-dark-tremor-brand dark:hover:bg-dark-tremor-brand-muted/40",
        className,
      )}
      {...props}
    >
      {loading ? (
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-current border-r-transparent" aria-hidden="true" />
      ) : Icon ? (
        <Icon className="h-4 w-4" />
      ) : null}
      {children}
    </button>
  );
}

type ProgressBarProps = Omit<HTMLAttributes<HTMLDivElement>, "color"> & {
  value: number;
  color?: Color;
};

export function ProgressBar({ value, color = "blue", className, ...props }: ProgressBarProps) {
  const normalizedValue = Math.min(100, Math.max(0, Number.isFinite(value) ? value : 0));
  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={normalizedValue}
      className={cn("h-2 overflow-hidden rounded-full bg-tremor-background-muted dark:bg-dark-tremor-background-muted", className)}
      {...props}
    >
      <div className={cn("h-full rounded-full transition-[width]", progressColors[color])} style={{ width: `${normalizedValue}%` }} />
    </div>
  );
}
