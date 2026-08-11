import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function FilterBar({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-wrap items-center gap-2 rounded-md border border-[#D9D9D6] bg-white p-3", className)}>
      {children}
    </div>
  );
}
