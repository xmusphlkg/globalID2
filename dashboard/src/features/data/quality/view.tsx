"use client";

import { CalendarDays, Clock, Database, ShieldCheck } from "lucide-react";

import { Chart } from "@/components/charts/Chart";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { CHART_TOKENS } from "@/lib/chart-theme";
import {
  useQualityCompleteness,
  useQualityGaps,
  useQualitySources,
  useQualityStats,
} from "@/features/data/api";
import { t } from "@/lib/i18n";
import { formatNumber } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";

function cadenceLabel(unit: string, mode: "noun" | "plural" | "adjective" = "noun") {
  if (unit === "week") {
    if (mode === "plural") return "Weeks";
    if (mode === "adjective") return "Weekly";
    return "Week";
  }
  if (unit === "biweek") {
    if (mode === "plural") return "Biweeks";
    if (mode === "adjective") return "Biweekly";
    return "Biweek";
  }
  if (mode === "plural") return "Months";
  if (mode === "adjective") return "Monthly";
  return "Month";
}

function ProgressLine({ value }: { value: number }) {
  const tone = value >= 90 ? "bg-emerald-500" : value >= 60 ? "bg-amber-500" : "bg-rose-500";

  return (
    <div className="flex min-w-[130px] items-center gap-3">
      <div className="h-2 flex-1 overflow-hidden rounded-tremor-full bg-tremor-background-muted dark:bg-dark-tremor-background-muted">
        <div className={`h-full rounded-tremor-full ${tone}`} style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />
      </div>
      <span className="w-12 text-right text-xs font-medium text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
        {value.toFixed(1)}%
      </span>
    </div>
  );
}

type CompletenessRow = NonNullable<ReturnType<typeof useQualityCompleteness>["data"]>[number];

export default function QualityPage() {
  const { lang, countryCode } = useAppStore();

  const { data: stats } = useQualityStats(countryCode || null);
  const { data: gaps } = useQualityGaps(countryCode || null);
  const { data: sources } = useQualitySources(countryCode || null);
  const { data: completeness } = useQualityCompleteness(countryCode || null, undefined, undefined, lang);
  const gapUnit = gaps?.[0]?.period_unit ?? "month";
  const completenessUnits = Array.from(new Set((completeness ?? []).map((item) => item.period_unit)));
  const mixedCadence = completenessUnits.length > 1;
  const cadenceSummary = mixedCadence
    ? "Adaptive"
    : cadenceLabel(completenessUnits[0] ?? gapUnit, "adjective");

  if (!countryCode) {
    return (
      <EmptyState
        icon={<ShieldCheck className="h-12 w-12" />}
        title={t(lang, "no_data")}
        description={lang === "zh" ? "请选择国家或地区后查看数据质量指标。" : "Select a country or region to view data quality metrics."}
        className="min-h-[55vh]"
      />
    );
  }

  const columns: DataTableColumn<CompletenessRow>[] = [
    {
      key: "disease",
      header: "Disease",
      render: (row) => (
        <span className="font-medium text-tremor-content-strong dark:text-dark-tremor-content-strong">
          {row.disease_name}
        </span>
      ),
    },
    {
      key: "cadence",
      header: "Cadence",
      render: (row) => <StatusBadge>{cadenceLabel(row.period_unit, "adjective")}</StatusBadge>,
    },
    {
      key: "periods",
      header: "Periods",
      render: (row) => (
        <span className="whitespace-nowrap text-sm text-tremor-content dark:text-dark-tremor-content">
          {row.data_periods} / {row.expected_periods}
        </span>
      ),
    },
    {
      key: "rate",
      header: "Rate",
      render: (row) => <ProgressLine value={row.completeness_rate} />,
    },
    {
      key: "records",
      header: "Records",
      render: (row) => (
        <span className="whitespace-nowrap text-sm text-tremor-content dark:text-dark-tremor-content">
          {formatNumber(row.total_records)}
        </span>
      ),
    },
    {
      key: "range",
      header: "Range",
      render: (row) => (
        <span className="whitespace-nowrap text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
          {row.earliest_date ?? "-"} - {row.latest_date ?? "-"}
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={t(lang, "mod_database")}
        title={t(lang, "quality")}
        description={
          lang === "zh"
            ? `数据完整性、时间缺口和来源结构分析（${cadenceSummary} 周期）。`
            : `Data quality, temporal gaps, and source coverage analysis (${cadenceSummary} periods).`
        }
        meta={
          <>
            <StatusBadge tone="primary">{cadenceSummary}</StatusBadge>
            <StatusBadge>{completeness?.length ?? 0} diseases</StatusBadge>
          </>
        }
      />

      {stats ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <MetricTile
            label={t(lang, "total_records")}
            value={stats.total_records}
            icon={<Database className="h-4 w-4" />}
            tone="primary"
          />
          <MetricTile
            label={t(lang, "total_diseases")}
            value={stats.unique_diseases}
            icon={<ShieldCheck className="h-4 w-4" />}
            tone="info"
          />
          <MetricTile
            label="Earliest"
            value={stats.earliest_date ?? "-"}
            icon={<CalendarDays className="h-4 w-4" />}
            tone="success"
            valueClassName="text-[17px] tracking-tight"
          />
          <MetricTile
            label="Latest"
            value={stats.latest_date ?? "-"}
            icon={<Clock className="h-4 w-4" />}
            tone="warning"
            valueClassName="text-[17px] tracking-tight"
          />
        </div>
      ) : null}

      {stats ? (
        <section className="grid gap-4 lg:grid-cols-2">
          <div className="app-panel p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                Zero Cases
              </h2>
              <StatusBadge tone="warning">{stats.zero_cases_pct}%</StatusBadge>
            </div>
            <ProgressLine value={stats.zero_cases_pct} />
            <p className="mt-3 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {formatNumber(stats.zero_cases_count)} records with zero cases
            </p>
          </div>

          <div className="app-panel p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                Zero Deaths
              </h2>
              <StatusBadge tone="warning">{stats.zero_deaths_pct}%</StatusBadge>
            </div>
            <ProgressLine value={stats.zero_deaths_pct} />
            <p className="mt-3 text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {formatNumber(stats.zero_deaths_count)} records with zero deaths
            </p>
          </div>
        </section>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        {gaps && gaps.length > 0 ? (
          <section className="app-panel p-4">
            <h2 className="mb-3 text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {t(lang, "time_gaps")}
            </h2>
            <Chart
              height={280}
              option={{
                tooltip: { trigger: "axis" },
                grid: { left: 60, right: 20, bottom: 50, top: 20 },
                xAxis: {
                  type: "time",
                  axisLabel: { rotate: 0, fontSize: 11, hideOverlap: true },
                },
                yAxis: { type: "value", name: `Gap (${cadenceLabel(gapUnit, "plural").toLowerCase()})`, splitLine: { lineStyle: { type: "dashed", color: CHART_TOKENS.gridLine } } },
                dataZoom: [{ type: "inside" }, { type: "slider", height: 20 }],
                series: [
                  {
                    type: "bar",
                    data: gaps.map((gap) => [gap.period_start, gap.gap_periods]),
                    barMaxWidth: 24,
                    itemStyle: { borderRadius: [4, 4, 0, 0] },
                  },
                ],
              }}
            />
          </section>
        ) : null}

        {sources && sources.length > 0 ? (
          <section className="app-panel p-4">
            <h2 className="mb-3 text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {t(lang, "data_sources")}
            </h2>
            <Chart
              height={280}
              option={{
                tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
                series: [
                  {
                    type: "pie",
                    radius: ["45%", "72%"],
                    center: ["50%", "50%"],
                    data: sources.map((source) => ({
                      name: source.data_source ?? "Unknown",
                      value: source.count,
                    })),
                    label: { show: true, formatter: "{b}\n{d}%", fontSize: 11 },
                  },
                ],
              }}
            />
          </section>
        ) : null}
      </div>

      {completeness && completeness.length > 0 ? (
        <section>
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {t(lang, "completeness")}
            </h2>
            <StatusBadge>{completeness.length}</StatusBadge>
          </div>
          <DataTable
            columns={columns}
            rows={completeness}
            getRowKey={(row) => row.disease_name}
            emptyState={<EmptyState title={t(lang, "no_data")} />}
          />
        </section>
      ) : null}
    </div>
  );
}
