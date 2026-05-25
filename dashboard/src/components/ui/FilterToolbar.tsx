import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface FilterToolbarProps {
  children: ReactNode;
  className?: string;
}

export function FilterToolbar({ children, className }: FilterToolbarProps) {
  return (
    <div
      className={cn(
        "rounded-tremor-default border border-tremor-border bg-tremor-background p-3 shadow-[0_1px_2px_rgba(23,33,31,0.04)] dark:border-dark-tremor-border dark:bg-dark-tremor-background",
        className,
      )}
    >
      <div className="flex flex-wrap items-center gap-2.5">{children}</div>
    </div>
  );
}
