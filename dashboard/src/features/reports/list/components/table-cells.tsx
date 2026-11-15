

import { type ReportListItem } from "@/features/reports/api";

export function QualityCell({ score }: { score: number | null | undefined }) {
  if (score == null) {
    return <span className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">--</span>;
  }

  const value = Math.round(score * 100);
  const tone = value >= 80 ? "bg-emerald-500" : value >= 50 ? "bg-amber-500" : "bg-rose-500";

  return (
    <div className="flex min-w-[132px] items-center gap-3">
      <div className="h-2 flex-1 overflow-hidden rounded-tremor-full bg-tremor-background-muted dark:bg-dark-tremor-background-muted">
        <div className={`h-full rounded-tremor-full ${tone}`} style={{ width: `${value}%` }} />
      </div>
      <span className="w-10 text-right text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {value}%
      </span>
    </div>
  );
}

export function ReportTitleCell({ report }: { report: ReportListItem }) {
  return (
    <div className="min-w-[260px] max-w-[520px]">
      <p className="line-clamp-2 font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
        {report.title}
      </p>
      <p className="mt-1 truncate text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {report.primary_disease || report.disease_names?.join(", ") || report.country_name || "-"}
      </p>
    </div>
  );
}
