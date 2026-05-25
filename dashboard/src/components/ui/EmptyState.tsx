import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface EmptyStateProps {
  icon?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  className?: string;
}

export function EmptyState({ icon, title, description, className }: EmptyStateProps) {
  return (
    <div className={cn("flex flex-col items-center justify-center rounded-tremor-default text-center", className)}>
      {icon ? (
        <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-tremor-default bg-tremor-background-muted text-tremor-content-subtle dark:bg-dark-tremor-background-muted dark:text-dark-tremor-content-subtle">
          {icon}
        </div>
      ) : null}
      <p className="text-sm font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">{title}</p>
      {description ? (
        <p className="mt-1 max-w-md text-sm text-tremor-content dark:text-dark-tremor-content">{description}</p>
      ) : null}
    </div>
  );
}
