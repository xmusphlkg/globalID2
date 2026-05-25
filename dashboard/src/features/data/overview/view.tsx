"use client";

import { useMemo, useState } from "react";
import { Activity, CalendarDays, Database, FileText, TrendingUp } from "lucide-react";

import { Chart } from "@/components/charts/Chart";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { CHART_TOKENS } from "@/lib/chart-theme";
import { useDiseases } from "@/lib/hooks/useDiseases";
import { useOverviewSummary, useOverviewTrend } from "@/lib/hooks/useOverview";
import { type ReportListItem, useReports } from "@/lib/hooks/useReports";
import { t } from "@/lib/i18n";
import { formatDate, formatNumber } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";

const intervals = [
  { labelKey: "interval_30d", value: 30 },
  { labelKey: "interval_90d", value: 90 },
  { labelKey: "interval_1y", value: 365 },
  { labelKey: "interval_all", value: null },
] as const;

export default function DataDashboardPage() {
  const { lang, countryId } = useAppStore();
  const { data: summary, isLoading } = useOverviewSummary(countryId, lang);
  const [interval, setInterval] = useState<number | null>(null);
  const [diseaseCode, setDiseaseCode] = useState<string | null>(null);

  const { data: diseases } = useDiseases(countryId, lang);
  const { data: trend } = useOverviewTrend(countryId, diseaseCode, interval);
  const { data: releaseReports } = useReports(countryId, undefined, 8);

  const releaseStats = useMemo(() => {
    const rows = releaseReports ?? [];
    const queue = rows.filter((report) => ["pending", "generating", "completed", "failed"].includes(report.status)).length;
    const review = rows.filter((report) => ["reviewing", "approved"].includes(report.status)).length;
    const published = rows.filter((report) => report.status === "published").length;
    return { queue, review, published };
  }, [releaseReports]);

  const focusLabel = diseaseCode
    ? diseases?.find((disease) => disease.code === diseaseCode)?.display_name || diseaseCode
    : t(lang, "all_diseases");

  const reportColumns = useMemo<DataTableColumn<ReportListItem>[]>(
    () => [
      {
        key: "status",
        header: lang === "zh" ? "状态" : "Status",
        render: (report) => <StatusBadge status={report.status}>{report.status}</StatusBadge>,
      },
      {
        key: "title",
        header: lang === "zh" ? "报告" : "Report",
        render: (report) => (
          <div className="min-w-[240px] max-w-[420px]">
            <p className="line-clamp-2 font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {report.title}
            </p>
            <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {report.section_count} {t(lang, "sections")}
            </p>
          </div>
        ),
      },
      {
        key: "created",
        header: lang === "zh" ? "创建时间" : "Created",
        render: (report) => (
          <span className="whitespace-nowrap text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
            {formatDate(report.created_at)}
          </span>
        ),
      },
    ],
    [lang],
  );

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={t(lang, "mod_database")}
        title={t(lang, "dashboard")}
        description={
          lang === "zh"
            ? "按当前国家汇总疾病指标、趋势和发布流转状态。"
            : "Country-level disease metrics, trend analysis, and release pipeline snapshot."
        }
        meta={
          <>
            <StatusBadge tone="primary">{focusLabel}</StatusBadge>
            <StatusBadge>{interval ? `${interval}d` : t(lang, "interval_all")}</StatusBadge>
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
        />
        <MetricTile
          label={t(lang, "latest_date")}
          value={summary?.latest_date ? formatDate(summary.latest_date) : "-"}
          icon={<CalendarDays className="h-4 w-4" />}
          tone="success"
        />
      </div>

      <FilterToolbar>
        <select
          value={diseaseCode || "all"}
          onChange={(event) => setDiseaseCode(event.target.value === "all" ? null : event.target.value)}
          className="h-10 min-w-[220px] rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
        >
          <option value="all">{t(lang, "all_diseases")}</option>
          {diseases?.map((disease) => (
            <option key={disease.code} value={disease.code}>
              {disease.display_name}
            </option>
          ))}
        </select>

        <select
          value={interval ? String(interval) : "all"}
          onChange={(event) => setInterval(event.target.value === "all" ? null : Number(event.target.value))}
          className="h-10 min-w-[150px] rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
        >
          {intervals.map((item) => (
            <option key={item.labelKey} value={item.value ? String(item.value) : "all"}>
              {t(lang, item.labelKey)}
            </option>
          ))}
        </select>
      </FilterToolbar>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <section className="rounded-tremor-default border border-tremor-border bg-tremor-background p-4 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {t(lang, "trend")}
              </h2>
              <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {focusLabel}
              </p>
            </div>
            <StatusBadge tone="info">{trend?.length ?? 0} points</StatusBadge>
          </div>

          {trend && trend.length > 0 ? (
            <Chart
              height={330}
              option={{
                tooltip: { trigger: "axis" },
                legend: { top: 0, data: [t(lang, "cases"), t(lang, "deaths")] },
                grid: { left: 74, right: 20, bottom: 52, top: 36 },
                dataZoom: [{ type: "inside" }, { type: "slider", height: 20 }],
                xAxis: {
                  type: "time",
                  axisLabel: { hideOverlap: true, fontSize: 11 },
                  boundaryGap: false,
                },
                yAxis: [
                  { type: "value", name: t(lang, "cases"), splitLine: { lineStyle: { type: "dashed", color: CHART_TOKENS.gridLine } } },
                  { type: "value", name: t(lang, "deaths"), splitLine: { show: false } },
                ],
                series: [
                  {
                    name: t(lang, "cases"),
                    type: "line",
                    data: trend.map((row) => [row.time_period, row.cases]),
                    smooth: true,
                    showSymbol: false,
                    areaStyle: { opacity: 0.08 },
                    lineStyle: { width: 2.5 },
                  },
                  {
                    name: t(lang, "deaths"),
                    type: "line",
                    yAxisIndex: 1,
                    data: trend.map((row) => [row.time_period, row.deaths]),
                    smooth: true,
                    showSymbol: false,
                    lineStyle: { width: 2.5 },
                  },
                ],
              }}
            />
          ) : (
            <EmptyState
              title={isLoading ? t(lang, "loading") : t(lang, "no_data")}
              className="h-[330px] rounded-tremor-default border border-dashed border-tremor-border dark:border-dark-tremor-border"
            />
          )}
        </section>

        <aside className="space-y-4">
          <section className="rounded-tremor-default border border-tremor-border bg-tremor-background p-4 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
            <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {t(lang, "top_diseases")}
            </h2>
            {summary?.top_diseases?.length ? (
              <div className="mt-4 space-y-2">
                {summary.top_diseases.slice(0, 5).map((disease, index) => (
                  <div
                    key={`${disease.name}-${index}`}
                    className="rounded-tremor-default border border-tremor-border bg-tremor-background-subtle px-3 py-3 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                          {disease.name}
                        </p>
                        <p className="mt-1 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                          {formatNumber(disease.total_cases)} {t(lang, "cases")} · {formatNumber(disease.total_deaths)} {t(lang, "deaths")}
                        </p>
                      </div>
                      <StatusBadge tone={index === 0 ? "success" : "neutral"}>{index + 1}</StatusBadge>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState title={t(lang, "no_data")} className="py-10" />
            )}
          </section>

          <section className="rounded-tremor-default border border-tremor-border bg-tremor-background p-4 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
            <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {t(lang, "release_snapshot")}
            </h2>
            <div className="mt-4 grid gap-2">
              <div className="flex items-center justify-between rounded-tremor-default bg-tremor-background-subtle px-3 py-2 dark:bg-dark-tremor-background-subtle">
                <span className="text-sm text-tremor-content dark:text-dark-tremor-content">{t(lang, "flow_queue")}</span>
                <StatusBadge>{releaseStats.queue}</StatusBadge>
              </div>
              <div className="flex items-center justify-between rounded-tremor-default bg-tremor-background-subtle px-3 py-2 dark:bg-dark-tremor-background-subtle">
                <span className="text-sm text-tremor-content dark:text-dark-tremor-content">{t(lang, "flow_review")}</span>
                <StatusBadge tone="info">{releaseStats.review}</StatusBadge>
              </div>
              <div className="flex items-center justify-between rounded-tremor-default bg-tremor-background-subtle px-3 py-2 dark:bg-dark-tremor-background-subtle">
                <span className="text-sm text-tremor-content dark:text-dark-tremor-content">{t(lang, "flow_published")}</span>
                <StatusBadge tone="success">{releaseStats.published}</StatusBadge>
              </div>
            </div>
          </section>
        </aside>
      </div>

      <section>
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
            {t(lang, "recent_releases")}
          </h2>
          <StatusBadge>{releaseReports?.length ?? 0}</StatusBadge>
        </div>
        <DataTable
          columns={reportColumns}
          rows={releaseReports ?? []}
          getRowKey={(report) => report.report_uuid}
          emptyState={<EmptyState icon={<FileText className="h-10 w-10" />} title={t(lang, "no_data")} />}
        />
      </section>
    </div>
  );
}
