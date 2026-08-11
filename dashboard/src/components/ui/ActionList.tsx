import Link from "next/link";
import { AlertCircle, AlertTriangle, ChevronRight, Info } from "lucide-react";

import { EmptyState } from "@/components/ui/EmptyState";

export interface ActionListItem {
  id: string;
  severity: string;
  category: string;
  title: string;
  detail: string;
  href: string;
  occurred_at: string;
}

const severityStyle: Record<string, { icon: typeof Info; className: string }> = {
  critical: { icon: AlertCircle, className: "bg-rose-50 text-rose-700" },
  warning: { icon: AlertTriangle, className: "bg-amber-50 text-amber-700" },
  info: { icon: Info, className: "bg-blue-50 text-blue-700" },
};

export function ActionList({ items }: { items: ActionListItem[] }) {
  if (!items.length) {
    return <EmptyState title="No action required" description="The control plane has no current operational blockers." className="min-h-48" />;
  }
  return (
    <div className="divide-y divide-[#E5E5E2]">
      {items.map((item) => {
        const style = severityStyle[item.severity] ?? severityStyle.info;
        const Icon = style.icon;
        return (
          <Link key={item.id} href={item.href} className="group flex items-start gap-3 px-4 py-3.5 transition hover:bg-[#F7F7F5]">
            <span className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md ${style.className}`}><Icon className="h-4 w-4" /></span>
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-2"><span className="text-sm font-semibold text-[#1D1D1F]">{item.title}</span><span className="text-[10px] font-semibold uppercase tracking-wide text-[#6B7280]">{item.category}</span></span>
              <span className="mt-0.5 line-clamp-2 block text-xs leading-5 text-[#6B7280]">{item.detail}</span>
            </span>
            <ChevronRight className="mt-2 h-4 w-4 shrink-0 text-[#9CA3AF] transition group-hover:translate-x-0.5 group-hover:text-[#C2410C]" />
          </Link>
        );
      })}
    </div>
  );
}
