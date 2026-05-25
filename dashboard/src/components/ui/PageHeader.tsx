import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface PageHeaderProps {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  meta?: ReactNode;
  className?: string;
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  meta,
  className,
}: PageHeaderProps) {
  return (
    <div
      className={cn(
        "flex flex-col gap-4 border-b border-tremor-border pb-5 dark:border-dark-tremor-border lg:flex-row lg:items-end lg:justify-between",
        className,
      )}
    >
      <div className="min-w-0 space-y-2">
        {eyebrow ? (
          <div className="inline-flex items-center rounded-tremor-default bg-tremor-background-muted px-2 py-1 text-[11px] font-semibold uppercase text-tremor-content-subtle dark:bg-dark-tremor-background-muted dark:text-dark-tremor-content-subtle">
            {eyebrow}
          </div>
        ) : null}
        <div>
          <h1 className="text-2xl font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong sm:text-[1.7rem]">
            {title}
          </h1>
          {description ? (
            <p className="mt-1 max-w-3xl text-sm text-tremor-content dark:text-dark-tremor-content">
              {description}
            </p>
          ) : null}
        </div>
        {meta ? <div className="flex flex-wrap items-center gap-2">{meta}</div> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}
