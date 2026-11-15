"use client";

import { Activity, CalendarDays, Database, TrendingUp } from "lucide-react";

import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";

import { t } from "@/lib/i18n";
import { formatDate } from "@/lib/utils";

import { useOverviewContext } from "./hooks/use-overview-context";
import { OverviewFilters } from "./overview-panels/filters";
import { TrendPanel } from "./overview-panels/trend-panel";
import { MonthlyPanel } from "./overview-panels/monthly-panel";
import { RecentReleases } from "./overview-panels/recent-releases";
import { FullscreenCharts } from "./overview-panels/fullscreen-charts";

export default function DataDashboardPage() {
  const context = useOverviewContext();
  const { lang, summary, isLoading, interval, hasCustomDateRange, trend, focusLabel, currentDateRangeLabel } = context;
  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={t(lang, "mod_database")}
        title={lang === "zh" ? t(lang, "dashboard") : "Jurisdiction Analytics"}
        description={
          lang === "zh"
            ? "按当前国家或地区汇总疾病指标、趋势和发布流转状态。"
            : "Disease metrics, trend analysis, and release pipeline snapshot for the selected jurisdiction."
        }
        meta={
          <>
            <StatusBadge tone="primary">{focusLabel}</StatusBadge>
            <StatusBadge>{hasCustomDateRange ? currentDateRangeLabel : interval ? `${interval}d` : t(lang, "interval_all")}</StatusBadge>
          </>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        <MetricTile
          label={t(lang, "total_diseases")}
          value={isLoading ? "-" : summary?.total_diseases ?? "-"}
          icon={<Activity className="h-4 w-4" />}
          tone="primary"
        />
        <MetricTile
          label={t(lang, "total_records")}
          value={isLoading ? "-" : summary?.total_records ?? "-"}
          icon={<Database className="h-4 w-4" />}
          tone="info"
        />
        <MetricTile
          label={t(lang, "recent_cases")}
          value={isLoading ? "-" : summary?.recent_cases_30d ?? "-"}
          icon={<TrendingUp className="h-4 w-4" />}
          tone="warning"
        />
        <MetricTile
          label={t(lang, "coverage_start")}
          value={summary?.earliest_date ? formatDate(summary.earliest_date) : "-"}
          icon={<CalendarDays className="h-4 w-4" />}
          tone="neutral"
          valueClassName="text-[17px] tracking-tight"
        />
        <MetricTile
          label={t(lang, "latest_date")}
          value={summary?.latest_date ? formatDate(summary.latest_date) : "-"}
          icon={<CalendarDays className="h-4 w-4" />}
          tone="success"
          valueClassName="text-[17px] tracking-tight"
        />
      </div>

      <OverviewFilters context={context} />

      <TrendPanel context={context} />

      <MonthlyPanel context={context} />

      <RecentReleases context={context} />

      <FullscreenCharts context={context} />

    </div>
  );
}
