import { useEffect, useMemo, useState } from "react";

import { echarts } from "@/components/charts/Chart";

import { CHART_TOKENS } from "@/lib/chart-theme";
import { useDiseases, useOverviewMonthlyComparison, useOverviewSummary, useOverviewTrend } from "@/features/data/api";
import { useReports } from "@/features/reports/api";
import { t } from "@/lib/i18n";

import { useAppStore } from "@/stores/app-store";

import { trendMetricTones, formatMetricValue, metricNumberFromChartValue, dateRangeLabel, type TrendMetric, type TrendChart } from "../presentation";
import { downloadChartCard } from "../export/chart-card";
export function useOverviewContext() {
  const { lang, countryCode, countryName } = useAppStore();
  const { data: summary, isLoading } = useOverviewSummary(countryCode || null, lang);
  const [interval, setInterval] = useState<number | null>(null);
  const [diseaseCode, setDiseaseCode] = useState<string | null>(null);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [trendMetric, setTrendMetric] = useState<TrendMetric>("cases");
  const [isTrendFullscreen, setIsTrendFullscreen] = useState(false);
  const [isMonthlyFullscreen, setIsMonthlyFullscreen] = useState(false);
  const [trendChart, setTrendChart] = useState<TrendChart | null>(null);
  const [fullscreenTrendChart, setFullscreenTrendChart] = useState<TrendChart | null>(null);
  const [monthlyChart, setMonthlyChart] = useState<TrendChart | null>(null);
  const [fullscreenMonthlyChart, setFullscreenMonthlyChart] = useState<TrendChart | null>(null);

  const { data: diseases } = useDiseases(countryCode || null, lang);
  useEffect(() => {
    if (diseaseCode && diseases && !diseases.some((disease) => disease.code === diseaseCode)) {
      setDiseaseCode(null);
    }
  }, [diseaseCode, diseases]);
  const hasCustomDateRange = Boolean(startDate || endDate);
  const { data: trend } = useOverviewTrend(
    countryCode || null,
    diseaseCode,
    hasCustomDateRange ? null : interval,
    startDate || null,
    endDate || null,
  );
  const { data: monthlyComparison } = useOverviewMonthlyComparison(
    countryCode || null,
    diseaseCode,
    hasCustomDateRange ? null : interval,
    startDate || null,
    endDate || null,
  );
  const { data: releaseReports } = useReports(countryCode || null, undefined, 8);

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

  const trendDates = useMemo(() => trend?.map((row) => row.time_period).filter(Boolean) ?? [], [trend]);
  const fallbackStartDate = trendDates[0];
  const fallbackEndDate = trendDates[trendDates.length - 1];
  const currentDateRangeLabel = dateRangeLabel(startDate, endDate, fallbackStartDate, fallbackEndDate, lang);
  const activeMetricTone = trendMetricTones[trendMetric];
  const activeMetricLabel = trendMetric === "cases" ? (lang === "zh" ? "发病数" : t(lang, "cases")) : t(lang, trendMetric);
  const activeMetricUnit = trendMetric === "incidence_rate" ? (lang === "zh" ? "每10万人" : "per 100k") : "";
  const chartSubtitle = `${countryName || t(lang, "country")} · ${focusLabel} · ${activeMetricLabel}`;
  const trendExportTitle = lang === "zh" ? "GIDS 疫情趋势" : "GIDS Epidemic Trend";
  const monthlyExportTitle = lang === "zh" ? "GIDS 逐年月度对比" : "GIDS Yearly Monthly Comparison";
  const monthLabels = useMemo(
    () =>
      Array.from({ length: 12 }, (_, index) =>
        lang === "zh" ? `${index + 1}月` : new Date(2024, index, 1).toLocaleString("en-US", { month: "short" }),
      ),
    [lang],
  );
  const metricButtons = useMemo(
    () =>
      ([
        { value: "cases", label: lang === "zh" ? "发病数" : t(lang, "cases") },
        { value: "deaths", label: t(lang, "deaths") },
        { value: "incidence_rate", label: t(lang, "incidence_rate") },
      ] as const).map((item) => ({ ...item, icon: trendMetricTones[item.value].icon })),
    [lang],
  );

  const trendOption = useMemo<echarts.EChartsCoreOption | null>(() => {
    if (!trend || trend.length === 0) return null;
    const data = trend.map((row) => [row.time_period, row[trendMetric]]);
    return {
      tooltip: {
        trigger: "axis",
        formatter: (params: unknown) => {
          const items = Array.isArray(params) ? params : [params];
          const first = items[0] as { axisValueLabel?: string; name?: string; value?: unknown; marker?: string; seriesName?: string } | undefined;
          const value = metricNumberFromChartValue(first?.value);
          const metricText = `${formatMetricValue(value, trendMetric)}${activeMetricUnit ? ` ${activeMetricUnit}` : ""}`;
          return `${first?.axisValueLabel || first?.name || ""}<br/>${first?.marker || ""}${first?.seriesName || activeMetricLabel}: ${metricText}`;
        },
      },
      legend: { top: 0, data: [activeMetricLabel] },
      grid: { left: 74, right: 28, bottom: 58, top: 42 },
      dataZoom: [
        { type: "inside", startValue: startDate || undefined, endValue: endDate || undefined },
        { type: "slider", height: 22, startValue: startDate || undefined, endValue: endDate || undefined },
      ],
      xAxis: {
        type: "time",
        axisLabel: { hideOverlap: true, fontSize: 11 },
        boundaryGap: false,
      },
      yAxis: {
        type: "value",
        name: activeMetricUnit ? `${activeMetricLabel} (${activeMetricUnit})` : activeMetricLabel,
        splitLine: { lineStyle: { type: "dashed", color: CHART_TOKENS.gridLine } },
      },
      series: [
        {
          name: activeMetricLabel,
          type: "line",
          data,
          smooth: true,
          showSymbol: false,
          areaStyle: { color: activeMetricTone.soft, opacity: 1 },
          lineStyle: { width: 2.8, color: activeMetricTone.color },
          itemStyle: { color: activeMetricTone.color },
          connectNulls: false,
        },
      ],
    };
  }, [activeMetricLabel, activeMetricTone.color, activeMetricTone.soft, activeMetricUnit, endDate, startDate, trend, trendMetric]);

  const monthlyComparisonOption = useMemo<echarts.EChartsCoreOption | null>(() => {
    if (!monthlyComparison || monthlyComparison.length === 0) return null;

    const years = Array.from(new Set(monthlyComparison.map((row) => row.year))).sort((left, right) => left - right);
    const valuesByYear = new Map<number, Array<number | null>>();
    years.forEach((year) => valuesByYear.set(year, Array.from({ length: 12 }, () => null)));
    monthlyComparison.forEach((row) => {
      const values = valuesByYear.get(row.year);
      if (!values || row.month < 1 || row.month > 12) return;
      values[row.month - 1] = row[trendMetric];
    });

    return {
      tooltip: {
        trigger: "axis",
        formatter: (params: unknown) => {
          const items = Array.isArray(params) ? params : [params];
          const first = items[0] as { axisValueLabel?: string; name?: string } | undefined;
          const lines = items
            .map((item) => {
              const current = item as { marker?: string; seriesName?: string; value?: unknown };
              const value = metricNumberFromChartValue(current.value);
              const metricText = `${formatMetricValue(value, trendMetric)}${activeMetricUnit ? ` ${activeMetricUnit}` : ""}`;
              return `${current.marker || ""}${current.seriesName || ""}: ${metricText}`;
            })
            .join("<br/>");
          return `${first?.axisValueLabel || first?.name || ""}<br/>${lines}`;
        },
      },
      legend: { top: 0, type: "scroll", data: years.map(String) },
      grid: { left: 74, right: 28, bottom: 58, top: 46 },
      dataZoom: [
        { type: "inside", xAxisIndex: 0 },
        { type: "slider", xAxisIndex: 0, height: 22 },
      ],
      xAxis: {
        type: "category",
        boundaryGap: false,
        data: monthLabels,
        axisLabel: { fontSize: 11 },
      },
      yAxis: {
        type: "value",
        name: activeMetricUnit ? `${activeMetricLabel} (${activeMetricUnit})` : activeMetricLabel,
        splitLine: { lineStyle: { type: "dashed", color: CHART_TOKENS.gridLine } },
      },
      series: years.map((year) => ({
        name: String(year),
        type: "line",
        data: valuesByYear.get(year) ?? [],
        smooth: true,
        symbol: "circle",
        symbolSize: 5,
        lineStyle: { width: 2.2 },
        emphasis: { focus: "series" },
        connectNulls: false,
      })),
    };
  }, [activeMetricLabel, activeMetricUnit, monthLabels, monthlyComparison, trendMetric]);

  const downloadDashboardChartImage = (chart: TrendChart | null, title: string, subtitle: string, filenamePrefix: string) => downloadChartCard(chart, title, subtitle, filenamePrefix, { lang, currentDateRangeLabel, activeMetricLabel, trendMetric });
  return { lang, countryCode, countryName, summary, isLoading, interval, setInterval, diseaseCode, setDiseaseCode, startDate, setStartDate, endDate, setEndDate, trendMetric, setTrendMetric, isTrendFullscreen, setIsTrendFullscreen, isMonthlyFullscreen, setIsMonthlyFullscreen, trendChart, setTrendChart, fullscreenTrendChart, setFullscreenTrendChart, monthlyChart, setMonthlyChart, fullscreenMonthlyChart, setFullscreenMonthlyChart, diseases, hasCustomDateRange, trend, monthlyComparison, releaseReports, releaseStats, focusLabel, trendDates, fallbackStartDate, fallbackEndDate, currentDateRangeLabel, activeMetricTone, activeMetricLabel, activeMetricUnit, chartSubtitle, trendExportTitle, monthlyExportTitle, monthLabels, metricButtons, trendOption, monthlyComparisonOption, downloadDashboardChartImage };
}
export type OverviewContext = ReturnType<typeof useOverviewContext>;
