"use client";

import { useEffect, useMemo, useState } from "react";
import { Activity, BarChart3, Percent, Skull } from "lucide-react";

import { Chart } from "@/components/charts/Chart";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { CHART_TOKENS } from "@/lib/chart-theme";
import { useCompare, useDiseaseRecords, useDiseases } from "@/features/data/api";
import { t } from "@/lib/i18n";
import { formatNumber } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";

export default function DiseasesPage() {
  const { lang, countryId } = useAppStore();
  const { data: diseases } = useDiseases(countryId, lang);

  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [compareMode, setCompareMode] = useState(false);
  const [compareCodes, setCompareCodes] = useState<string[]>([]);

  const { data: records } = useDiseaseRecords(compareMode ? null : selectedCode, countryId);
  const { data: compareData } = useCompare(compareMode ? countryId : null, compareCodes);

  useEffect(() => {
    if (!compareMode && !selectedCode && diseases && diseases.length > 0) {
      setSelectedCode(diseases[0].code);
    }
  }, [compareMode, selectedCode, diseases]);

  const compareXAxis = useMemo(() => {
    if (!compareData?.diseases) return [] as string[];
    return Array.from(
      new Set(compareData.diseases.flatMap((disease) => disease.data.map((row) => row.time_period))),
    ).sort();
  }, [compareData]);

  if (!countryId) {
    return (
      <EmptyState
        icon={<Activity className="h-12 w-12" />}
        title={t(lang, "no_data")}
        description={lang === "zh" ? "请选择国家后分析疾病数据。" : "Select a country to analyze diseases."}
        className="min-h-[55vh]"
      />
    );
  }

  const totalCases = records?.reduce((sum, row) => sum + (row.cases ?? 0), 0) ?? 0;
  const totalDeaths = records?.reduce((sum, row) => sum + (row.deaths ?? 0), 0) ?? 0;
  const cfr = totalCases > 0 ? ((totalDeaths / totalCases) * 100).toFixed(2) : "0.00";
  const avgMonthly = records && records.length > 0 ? Math.round(totalCases / records.length) : 0;
  const selectedDiseaseName = selectedCode
    ? diseases?.find((disease) => disease.code === selectedCode)?.display_name || selectedCode
    : t(lang, "all_diseases");

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow={t(lang, "mod_database")}
        title={t(lang, "diseases")}
        description={
          lang === "zh"
            ? "查看单个疾病的病例/死亡趋势，或横向比较多个疾病。"
            : "Analyze a single disease trend or compare multiple diseases side by side."
        }
        meta={
          <>
            <StatusBadge tone={compareMode ? "info" : "primary"}>
              {compareMode ? (lang === "zh" ? "对比模式" : "Compare mode") : selectedDiseaseName}
            </StatusBadge>
            <StatusBadge>{diseases?.length ?? 0} {lang === "zh" ? "个疾病" : "diseases"}</StatusBadge>
          </>
        }
      />

      <FilterToolbar>
        <div className="flex items-center gap-1 rounded-tremor-default border border-tremor-border bg-tremor-background-subtle p-1 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
          <button
            type="button"
            className={`h-8 rounded-tremor-default px-3 text-sm font-medium transition ${
              !compareMode
                ? "bg-tremor-background text-tremor-content-strong dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
                : "text-tremor-content-subtle hover:text-tremor-content-strong dark:text-dark-tremor-content-subtle dark:hover:text-dark-tremor-content-strong"
            }`}
            onClick={() => setCompareMode(false)}
          >
            {lang === "zh" ? "分析" : "Analysis"}
          </button>
          <button
            type="button"
            className={`h-8 rounded-tremor-default px-3 text-sm font-medium transition ${
              compareMode
                ? "bg-tremor-background text-tremor-content-strong dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
                : "text-tremor-content-subtle hover:text-tremor-content-strong dark:text-dark-tremor-content-subtle dark:hover:text-dark-tremor-content-strong"
            }`}
            onClick={() => setCompareMode(true)}
          >
            {lang === "zh" ? "对比" : "Compare"}
          </button>
        </div>

        {compareMode ? (
          <select
            multiple
            value={compareCodes}
            onChange={(event) => {
              const values = Array.from(event.currentTarget.selectedOptions).map((option) => option.value);
              setCompareCodes(values);
            }}
            className="min-h-10 min-w-[280px] rounded-tremor-default border border-tremor-border bg-tremor-background px-3 py-2 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
          >
            {diseases?.map((disease) => (
              <option key={disease.code} value={disease.code}>
                {disease.display_name}
              </option>
            ))}
          </select>
        ) : (
          <select
            value={selectedCode || "all"}
            onChange={(event) => setSelectedCode(event.target.value === "all" ? null : event.target.value)}
            className="h-10 min-w-[280px] rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
          >
            <option value="all">{t(lang, "all_diseases")}</option>
            {diseases?.map((disease) => (
              <option key={disease.code} value={disease.code}>
                {disease.display_name}
              </option>
            ))}
          </select>
        )}
      </FilterToolbar>

      {!compareMode ? (
        <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <MetricTile
              label={t(lang, "total_cases")}
              value={formatNumber(totalCases)}
              icon={<Activity className="h-4 w-4" />}
              tone="warning"
            />
            <MetricTile
              label={t(lang, "total_deaths")}
              value={formatNumber(totalDeaths)}
              icon={<Skull className="h-4 w-4" />}
              tone="danger"
            />
            <MetricTile
              label={t(lang, "cfr")}
              value={`${cfr}%`}
              icon={<Percent className="h-4 w-4" />}
              tone="info"
            />
            <MetricTile
              label={t(lang, "avg_monthly")}
              value={formatNumber(avgMonthly)}
              icon={<BarChart3 className="h-4 w-4" />}
              tone="primary"
            />
          </div>

          <section className="app-panel p-4">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                  {t(lang, "cases_trend")}
                </h2>
                <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                  {selectedDiseaseName}
                </p>
              </div>
              <StatusBadge>{records?.length ?? 0} points</StatusBadge>
            </div>
            <div className="h-96">
              {records && records.length > 0 ? (
                <Chart
                  option={{
                    tooltip: { trigger: "axis" },
                    legend: { top: 0, data: [t(lang, "cases"), t(lang, "deaths")] },
                    grid: { left: 60, right: 20, bottom: 40, top: 40 },
                    xAxis: {
                      type: "time",
                      boundaryGap: false,
                    },
                    yAxis: [
                      { type: "value", name: t(lang, "cases"), splitLine: { lineStyle: { type: "dashed", color: CHART_TOKENS.gridLine } } },
                      { type: "value", name: t(lang, "deaths"), show: false },
                    ],
                    dataZoom: [{ type: "inside" }, { type: "slider", height: 20 }],
                    series: [
                      {
                        name: t(lang, "cases"),
                        type: "line",
                        data: records.map((row) => [row.time, row.cases]),
                        smooth: true,
                        areaStyle: { opacity: 0.1 },
                        lineStyle: { width: 3 },
                        itemStyle: { color: CHART_TOKENS.warning },
                      },
                      {
                        name: t(lang, "deaths"),
                        type: "line",
                        yAxisIndex: 1,
                        data: records.map((row) => [row.time, row.deaths]),
                        smooth: true,
                        lineStyle: { width: 3 },
                        itemStyle: { color: CHART_TOKENS.destructive },
                      },
                    ],
                  }}
                />
              ) : (
                <EmptyState
                  title={t(lang, "no_data")}
                  className="h-full rounded-tremor-default border border-dashed border-tremor-border dark:border-dark-tremor-border"
                />
              )}
            </div>
          </section>
        </>
      ) : (
        <section className="app-panel p-4">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {lang === "zh" ? "疾病对比" : "Disease Comparison"}
              </h2>
              <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {compareCodes.length} {lang === "zh" ? "个疾病已选择" : "selected"}
              </p>
            </div>
            <StatusBadge tone="info">{compareData?.diseases.length ?? 0}</StatusBadge>
          </div>
          <div className="h-96">
            {compareData && compareData.diseases.length > 0 && compareCodes.length > 0 ? (
              <Chart
                option={{
                  tooltip: { trigger: "axis" },
                  legend: { top: 0, data: compareData.diseases.map((disease) => disease.disease_name) },
                  grid: { left: 50, right: 20, bottom: 40, top: 40 },
                  xAxis: {
                    type: "time",
                  },
                  yAxis: { type: "value", splitLine: { lineStyle: { type: "dashed", color: CHART_TOKENS.gridLine } } },
                  dataZoom: [{ type: "inside" }, { type: "slider", height: 20 }],
                  series: compareData.diseases.map((disease) => ({
                    name: disease.disease_name,
                    type: "line",
                    smooth: true,
                    data: compareXAxis.map((period) => {
                      const match = disease.data.find((row) => row.time_period === period);
                      return [period, match ? match.cases : 0];
                    }),
                    lineStyle: { width: 2 },
                  })),
                }}
              />
            ) : (
              <EmptyState
                title={lang === "zh" ? "请选择多个疾病进行对比" : "Select multiple diseases to compare"}
                className="h-full rounded-tremor-default border border-dashed border-tremor-border dark:border-dark-tremor-border"
              />
            )}
          </div>
        </section>
      )}
    </div>
  );
}
