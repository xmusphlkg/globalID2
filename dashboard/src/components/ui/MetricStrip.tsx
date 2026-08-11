import type { LucideIcon } from "lucide-react";

export interface MetricStripItem {
  label: string;
  value: string | number;
  detail?: string;
  icon?: LucideIcon;
  tone?: "neutral" | "success" | "warning" | "danger";
}

const tones = {
  neutral: "text-[#374151] bg-[#F7F7F5]",
  success: "text-emerald-700 bg-emerald-50",
  warning: "text-amber-700 bg-amber-50",
  danger: "text-rose-700 bg-rose-50",
};

export function MetricStrip({ items }: { items: MetricStripItem[] }) {
  return (
    <div className="metric-strip">
      {items.map((item) => {
        const Icon = item.icon;
        return (
          <div key={item.label} className="min-w-0 px-4 py-4">
            <div className="flex items-center justify-between gap-3">
              <p className="truncate text-xs font-medium text-[#6B7280]">{item.label}</p>
              {Icon ? <span className={`flex h-7 w-7 items-center justify-center rounded-md ${tones[item.tone ?? "neutral"]}`}><Icon className="h-4 w-4" /></span> : null}
            </div>
            <p className="mt-2 text-2xl font-semibold tracking-tight text-[#1D1D1F]">{item.value}</p>
            {item.detail ? <p className="mt-1 truncate text-xs text-[#6B7280]">{item.detail}</p> : null}
          </div>
        );
      })}
    </div>
  );
}
