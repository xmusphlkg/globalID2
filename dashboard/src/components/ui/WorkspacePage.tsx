import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function WorkspacePage({
  title,
  description,
  eyebrow,
  actions,
  children,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  eyebrow?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("space-y-5", className)}>
      <header className="workspace-header">
        <div className="min-w-0">
          {eyebrow ? <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-[#C2410C]">{eyebrow}</p> : null}
          <h1 className="workspace-title">{title}</h1>
          {description ? <p className="workspace-description">{description}</p> : null}
        </div>
        {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
      </header>
      {children}
    </div>
  );
}
