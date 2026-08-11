import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export interface TabItem {
  id: string;
  label: ReactNode;
  badge?: ReactNode;
}

export function Tabs({ items, value, onChange, className }: { items: TabItem[]; value: string; onChange: (id: string) => void; className?: string }) {
  return (
    <div role="tablist" className={cn("flex gap-1 border-b border-[#D9D9D6]", className)}>
      {items.map((item) => <button key={item.id} type="button" role="tab" aria-selected={item.id === value} onClick={() => onChange(item.id)} className={cn("-mb-px inline-flex h-10 items-center gap-2 border-b-2 px-3 text-sm font-semibold", item.id === value ? "border-[#C2410C] text-[#C2410C]" : "border-transparent text-[#6B7280] hover:text-[#1D1D1F]")}>{item.label}{item.badge}</button>)}
    </div>
  );
}
