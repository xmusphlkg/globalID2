"use client";

import { Chart, echarts } from "@/components/charts/Chart";

type RecordValue = Record<string, unknown>;

interface ReportFigureListProps {
  figures: RecordValue[];
  figureData: RecordValue;
  lang: "en" | "zh";
  placement?: "before_content" | "after_content";
}

function asRecord(value: unknown): RecordValue {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as RecordValue) : {};
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function numberArray(value: unknown): Array<number | null> {
  return asArray(value).map((item) => {
    const numeric = typeof item === "number" ? item : Number(item);
    return Number.isFinite(numeric) ? numeric : null;
  });
}

function formatNumber(value: unknown): string {
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return value == null || value === "" ? "N/A" : String(value);
  return numeric.toLocaleString(undefined, { maximumFractionDigits: 3 });
}

function rollingMean(values: Array<number | null>, windowSize: number): Array<number | null> {
  return values.map((_value, index) => {
    const windowValues = values
      .slice(Math.max(0, index - windowSize + 1), index + 1)
      .filter((item): item is number => item !== null && Number.isFinite(item));
    if (windowValues.length < 2) return null;
    return Math.round((windowValues.reduce((sum, item) => sum + item, 0) / windowValues.length) * 100) / 100;
  });
}

function riskColor(level: unknown) {
  const key = String(level || "low").toLowerCase();
  return (
    {
      critical: "#991b1b",
      high: "#b91c1c",
      moderate: "#b45309",
      low: "#0f766e",
    }[key] || "#0f766e"
  );
}

function dataSeries(figure: RecordValue, figureData: RecordValue): RecordValue {
  const dataKey = asString(figure.data_key) || `disease:${asString(figure.disease_id)}`;
  return asRecord(asRecord(figureData.series)[dataKey]);
}

function epidemicCurveOption(figure: RecordValue, figureData: RecordValue, lang: "en" | "zh"): echarts.EChartsCoreOption | null {
  const series = dataSeries(figure, figureData);
  const periods = asArray(series.periods).map(String);
  const cases = numberArray(series.cases);
  if (periods.length < 2 || cases.length < 2) return null;

  const visual = asRecord(series.visual);
  const caseSeries: RecordValue = {
    name: lang === "zh" ? "报告病例" : "Reported cases",
    type: "line",
    data: cases,
    symbol: "circle",
    symbolSize: 6,
    lineStyle: { width: 2.5, color: "#116a8c" },
    itemStyle: { color: "#116a8c" },
    emphasis: { focus: "series" },
  };
  const median = asNumber(visual.pre_latest_median_cases);
  if (median !== null) {
    caseSeries.markLine = {
      silent: true,
      symbol: "none",
      lineStyle: { type: "dashed", color: "#64748b", width: 1.5 },
      label: { formatter: lang === "zh" ? "最新期前中位数" : "pre-latest median" },
      data: [{ yAxis: median }],
    };
  }

  const chartSeries: RecordValue[] = [caseSeries];
  const mean = rollingMean(cases, 3);
  if (mean.some((item) => item !== null)) {
    chartSeries.push({
      name: lang === "zh" ? "3期均值" : "3-period mean",
      type: "line",
      data: mean,
      symbol: "none",
      connectNulls: true,
      lineStyle: { width: 2, type: "dotted", color: "#b45309" },
      itemStyle: { color: "#b45309" },
    });
  }

  const peakPeriod = asString(visual.peak_period);
  const peakCases = asNumber(visual.peak_cases);
  const peakIndex = periods.indexOf(peakPeriod);
  if (peakIndex >= 0 && peakCases !== null) {
    chartSeries.push({
      name: lang === "zh" ? "观察峰值" : "Observed peak",
      type: "scatter",
      data: [[peakIndex, peakCases]],
      symbol: "diamond",
      symbolSize: 12,
      itemStyle: { color: "#b91c1c" },
    });
  }

  return {
    animation: false,
    legend: { top: 0, right: 0, type: "scroll" },
    grid: { left: 54, right: 22, top: 48, bottom: 74 },
    tooltip: { trigger: "axis", confine: true },
    aria: { enabled: true },
    xAxis: {
      type: "category",
      data: periods,
      axisLabel: { rotate: periods.length > 10 ? 35 : 0, color: "#5d6978" },
      axisLine: { lineStyle: { color: "#d7dde5" } },
    },
    yAxis: {
      type: "value",
      name: lang === "zh" ? "病例数" : "Cases",
      min: 0,
      axisLabel: { color: "#5d6978" },
      splitLine: { lineStyle: { color: "#e7ebf0", type: "dashed" } },
    },
    series: chartSeries,
  };
}

function casesIncidencePanelOption(figure: RecordValue, figureData: RecordValue, lang: "en" | "zh"): echarts.EChartsCoreOption | null {
  const series = dataSeries(figure, figureData);
  const periods = asArray(series.periods).map(String);
  const cases = numberArray(series.cases);
  const incidence = numberArray(series.incidence_rate_per_100k);
  if (periods.length < 2 || !incidence.some((item) => item !== null)) return null;

  return {
    animation: false,
    legend: { top: 0, right: 0, type: "scroll" },
    tooltip: { trigger: "axis", confine: true },
    aria: { enabled: true },
    grid: [
      { left: 58, right: 24, top: 48, height: "40%" },
      { left: 58, right: 24, bottom: 70, height: "30%" },
    ],
    xAxis: [
      { type: "category", data: periods, gridIndex: 0, axisLabel: { show: false }, axisLine: { lineStyle: { color: "#d7dde5" } } },
      { type: "category", data: periods, gridIndex: 1, axisLabel: { rotate: periods.length > 10 ? 35 : 0, color: "#5d6978" }, axisLine: { lineStyle: { color: "#d7dde5" } } },
    ],
    yAxis: [
      { type: "value", name: lang === "zh" ? "病例数" : "Cases", gridIndex: 0, min: 0, axisLabel: { color: "#5d6978" }, splitLine: { lineStyle: { color: "#e7ebf0", type: "dashed" } } },
      { type: "value", name: lang === "zh" ? "每10万人" : "Per 100k", gridIndex: 1, min: 0, axisLabel: { color: "#5d6978" }, splitLine: { lineStyle: { color: "#e7ebf0", type: "dashed" } } },
    ],
    series: [
      {
        name: lang === "zh" ? "报告病例" : "Reported cases",
        type: "bar",
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: cases,
        barMaxWidth: 18,
        itemStyle: { color: "#116a8c", opacity: 0.86 },
      },
      {
        name: lang === "zh" ? "每10万人粗发病率" : "Crude incidence per 100k",
        type: "line",
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: incidence,
        symbol: "circle",
        symbolSize: 5,
        lineStyle: { width: 2.5, color: "#b45309" },
        itemStyle: { color: "#b45309" },
      },
    ],
  };
}

function signalContextPanelOption(figure: RecordValue, figureData: RecordValue, lang: "en" | "zh"): echarts.EChartsCoreOption | null {
  const series = dataSeries(figure, figureData);
  const cases = numberArray(series.cases);
  const visual = asRecord(series.visual);
  const latest = cases.length > 0 ? cases[cases.length - 1] : null;
  const rows = [
    { label: lang === "zh" ? "最新期病例" : "Latest cases", value: latest, color: "#116a8c" },
    { label: lang === "zh" ? "上一期病例" : "Previous cases", value: visual.previous_cases, color: "#64748b" },
    { label: lang === "zh" ? "最新期前中位数" : "Pre-latest median", value: visual.pre_latest_median_cases, color: "#0f766e" },
    { label: lang === "zh" ? "3期滚动均值" : "3-period mean", value: visual.rolling_mean_cases, color: "#b45309" },
    { label: lang === "zh" ? "最近4期病例" : "Latest 4 periods", value: visual.latest_4_period_cases, color: "#b91c1c" },
    { label: lang === "zh" ? "前4期病例" : "Previous 4 periods", value: visual.previous_4_period_cases, color: "#94a3b8" },
  ]
    .filter((row) => row.value !== null && row.value !== undefined && Number.isFinite(Number(row.value)))
    .reverse();
  if (rows.length === 0) return null;

  const anomaly = asRecord(visual.anomaly);
  const subtitleParts: string[] = [];
  if (visual.last4_change_pct !== null && visual.last4_change_pct !== undefined) {
    subtitleParts.push(`${lang === "zh" ? "近4期变化 " : "4-period change "}${formatNumber(visual.last4_change_pct)}%`);
  }
  if (visual.latest_to_baseline_ratio !== null && visual.latest_to_baseline_ratio !== undefined) {
    subtitleParts.push(`${lang === "zh" ? "最新/基线 " : "latest/baseline "}${formatNumber(visual.latest_to_baseline_ratio)}x`);
  }
  if (anomaly.robust_z !== null && anomaly.robust_z !== undefined) {
    subtitleParts.push(`MAD z ${formatNumber(anomaly.robust_z)}`);
  }

  return {
    animation: false,
    tooltip: {
      trigger: "item",
      confine: true,
      formatter: (params: { name?: string; value?: unknown }) => `${params.name || ""}<br/>${formatNumber(params.value)} cases`,
    },
    title:
      subtitleParts.length > 0
        ? {
            text: subtitleParts.join(" · "),
            left: 4,
            top: 0,
            textStyle: { color: "#5d6978", fontSize: 12, fontWeight: 500 },
          }
        : undefined,
    aria: { enabled: true },
    grid: { left: 142, right: 28, top: subtitleParts.length > 0 ? 42 : 18, bottom: 38 },
    xAxis: {
      type: "value",
      name: lang === "zh" ? "病例数" : "Cases",
      min: 0,
      axisLabel: { color: "#5d6978" },
      splitLine: { lineStyle: { color: "#e7ebf0", type: "dashed" } },
    },
    yAxis: {
      type: "category",
      data: rows.map((row) => row.label),
      axisLabel: { color: "#5d6978" },
      axisLine: { lineStyle: { color: "#d7dde5" } },
    },
    series: [
      {
        name: lang === "zh" ? "证据窗口" : "Evidence window",
        type: "bar",
        barMaxWidth: 18,
        data: rows.map((row) => ({ value: Number(row.value), itemStyle: { color: row.color } })),
        label: { show: true, position: "right", formatter: (params: { value?: unknown }) => formatNumber(params.value), color: "#263647" },
      },
    ],
  };
}

function recentWindowHeatmapOption(figure: RecordValue, figureData: RecordValue, lang: "en" | "zh"): echarts.EChartsCoreOption | null {
  const series = dataSeries(figure, figureData);
  const periods = asArray(series.periods).map(String).slice(-52);
  const cases = numberArray(series.cases).slice(-52);
  if (periods.length < 4) return null;

  const columns = periods.length >= 13 ? 13 : Math.max(periods.length, 1);
  const rows = Math.ceil(periods.length / columns);
  const cells = periods.map((period, index) => [index % columns, Math.floor(index / columns), cases[index] ?? 0, period]);
  const maxValue = cells.reduce((max, item) => Math.max(max, Number(item[2]) || 0), 0);

  return {
    animation: false,
    tooltip: {
      trigger: "item",
      confine: true,
      formatter: (params: { data?: unknown }) => {
        const item = Array.isArray(params.data) ? params.data : [];
        return `${String(item[3] || "")}<br/>Cases: ${formatNumber(item[2])}`;
      },
    },
    aria: { enabled: true },
    grid: { left: 70, right: 72, top: 18, bottom: 54 },
    xAxis: {
      type: "category",
      data: Array.from({ length: columns }, (_value, index) => String(index + 1)),
      name: lang === "zh" ? "近期报告期序列" : "Sequential recent periods",
      axisLabel: { color: "#5d6978" },
      axisLine: { lineStyle: { color: "#d7dde5" } },
    },
    yAxis: {
      type: "category",
      data: Array.from({ length: rows }, (_value, index) => (lang === "zh" ? `分块 ${index + 1}` : `Block ${index + 1}`)),
      axisLabel: { color: "#5d6978" },
      axisLine: { lineStyle: { color: "#d7dde5" } },
    },
    visualMap: {
      min: 0,
      max: maxValue,
      calculable: true,
      orient: "vertical",
      right: 8,
      top: 28,
      inRange: { color: ["#f7fafc", "#9ecae1", "#b91c1c"] },
    },
    series: [
      {
        name: lang === "zh" ? "病例" : "Cases",
        type: "heatmap",
        data: cells,
        label: { show: false },
        emphasis: { itemStyle: { shadowBlur: 8, shadowColor: "rgba(17, 106, 140, 0.25)" } },
      },
    ],
  };
}

function riskRankingBarOption(figureData: RecordValue, lang: "en" | "zh"): echarts.EChartsCoreOption | null {
  const rows = asArray(figureData.attention_ranking || figureData.risk_ranking).map(asRecord).slice(0, 10).reverse();
  if (rows.length < 2) return null;

  return {
    animation: false,
    tooltip: {
      trigger: "item",
      confine: true,
      formatter: (params: { name?: string; data?: unknown }) => {
        const item = asRecord(params.data);
        const change = item.change_pct == null ? "N/A" : `${formatNumber(item.change_pct)}%`;
        const scoreLabel = lang === "zh" ? "监测关注分" : "Attention score";
        const bandLabel = lang === "zh" ? "复核优先级分档" : "Review-priority band";
        return `${params.name || ""}<br/>${scoreLabel}: ${formatNumber(item.value)}<br/>${bandLabel}: ${asString(item.level) || "N/A"}<br/>Latest cases: ${formatNumber(item.latest_cases)}<br/>Change: ${change}`;
      },
    },
    aria: { enabled: true },
    grid: { left: 150, right: 24, top: 20, bottom: 44 },
    xAxis: { type: "value", name: lang === "zh" ? "监测关注分" : "Attention score", min: 0, max: 100, axisLabel: { color: "#5d6978" }, splitLine: { lineStyle: { color: "#e7ebf0", type: "dashed" } } },
    yAxis: { type: "category", data: rows.map((row) => asString(row.name) || "Unknown"), axisLabel: { color: "#5d6978" }, axisLine: { lineStyle: { color: "#d7dde5" } } },
    series: [
      {
        name: lang === "zh" ? "监测关注优先级" : "Surveillance attention priority",
        type: "bar",
        barMaxWidth: 18,
        data: rows.map((row) => ({
          value: Number(row.attention_score ?? row.risk_score ?? 0),
          level: row.attention_level || row.risk_level || "low",
          latest_cases: Number(row.latest_cases || 0),
          change_pct: row.change_pct,
          itemStyle: { color: riskColor(row.attention_level || row.risk_level) },
        })),
      },
    ],
  };
}

function seasonalBaselineBandOption(figure: RecordValue, figureData: RecordValue, lang: "en" | "zh"): echarts.EChartsCoreOption | null {
  const series = dataSeries(figure, figureData);
  const periods = asArray(series.periods).map(String);
  const cases = numberArray(series.cases);
  if (periods.length < 2 || cases.length < 2) return null;
  const visual = asRecord(series.visual);
  const derived = asRecord(visual.derived);
  const lower = numberArray(derived.baseline_lower);
  const upper = numberArray(derived.baseline_upper);
  const bandWidth = upper.map((value, index) => (value === null || lower[index] === null ? null : Math.max(0, value - Number(lower[index]))));
  const chartSeries: RecordValue[] = [
    { name: lang === "zh" ? "背景带下界" : "Baseline lower", type: "line", data: lower, stack: "baseline-band", symbol: "none", lineStyle: { opacity: 0 }, itemStyle: { opacity: 0 }, emphasis: { disabled: true } },
    { name: lang === "zh" ? "背景带" : "Baseline band", type: "line", data: bandWidth, stack: "baseline-band", symbol: "none", lineStyle: { opacity: 0 }, areaStyle: { color: "rgba(17,106,140,0.14)" }, itemStyle: { opacity: 0 }, emphasis: { disabled: true } },
    { name: lang === "zh" ? "报告病例" : "Reported cases", type: "line", data: cases, symbol: "circle", symbolSize: 5, lineStyle: { width: 2.5, color: "#116a8c" }, itemStyle: { color: "#116a8c" } },
    { name: lang === "zh" ? "3期均值" : "3-period mean", type: "line", data: numberArray(derived.rolling_mean_3), symbol: "none", connectNulls: true, lineStyle: { width: 2, type: "dotted", color: "#b45309" } },
  ];
  const median = asNumber(visual.pre_latest_median_cases);
  if (median !== null) {
    chartSeries[2].markLine = { silent: true, symbol: "none", lineStyle: { type: "dashed", color: "#64748b" }, data: [{ yAxis: median }] };
  }
  return {
    animation: false,
    tooltip: { trigger: "axis", confine: true },
    legend: { top: 0, right: 0, type: "scroll" },
    aria: { enabled: true },
    grid: { left: 56, right: 24, top: 50, bottom: 74 },
    xAxis: { type: "category", data: periods, axisLabel: { rotate: periods.length > 10 ? 35 : 0, color: "#5d6978" }, axisLine: { lineStyle: { color: "#d7dde5" } } },
    yAxis: { type: "value", name: lang === "zh" ? "病例数" : "Cases", min: 0, axisLabel: { color: "#5d6978" }, splitLine: { lineStyle: { color: "#e7ebf0", type: "dashed" } } },
    series: chartSeries,
  };
}

function anomalyMarkerCurveOption(figure: RecordValue, figureData: RecordValue, lang: "en" | "zh"): echarts.EChartsCoreOption | null {
  const series = dataSeries(figure, figureData);
  const periods = asArray(series.periods).map(String);
  const cases = numberArray(series.cases);
  if (periods.length < 4 || cases.length < 4) return null;
  const visual = asRecord(series.visual);
  const derived = asRecord(visual.derived);
  const latestIndex = Math.max(0, periods.length - 1);
  const peakPeriod = asString(visual.peak_period);
  const peakIndex = periods.indexOf(peakPeriod);
  const threshold = numberArray(derived.anomaly_threshold).find((item) => item !== null);
  return {
    animation: false,
    tooltip: { trigger: "axis", confine: true },
    legend: { top: 0, right: 0, type: "scroll" },
    aria: { enabled: true },
    grid: { left: 56, right: 24, top: 50, bottom: 74 },
    xAxis: { type: "category", data: periods, axisLabel: { rotate: periods.length > 10 ? 35 : 0, color: "#5d6978" }, axisLine: { lineStyle: { color: "#d7dde5" } } },
    yAxis: { type: "value", name: lang === "zh" ? "病例数" : "Cases", min: 0, axisLabel: { color: "#5d6978" }, splitLine: { lineStyle: { color: "#e7ebf0", type: "dashed" } } },
    series: [
      {
        name: lang === "zh" ? "报告病例" : "Reported cases",
        type: "line",
        data: cases,
        symbol: "circle",
        symbolSize: 5,
        lineStyle: { width: 2.4, color: "#116a8c" },
        itemStyle: { color: "#116a8c" },
        markLine: threshold === undefined ? undefined : { silent: true, symbol: "none", lineStyle: { type: "dashed", color: "#b91c1c", width: 1.5 }, label: { formatter: lang === "zh" ? "异常阈值" : "alert threshold" }, data: [{ yAxis: threshold }] },
      },
      { name: lang === "zh" ? "最新期" : "Latest", type: "scatter", data: [[latestIndex, Number(cases[latestIndex] || 0)]], symbolSize: 13, itemStyle: { color: "#b91c1c" } },
      { name: lang === "zh" ? "峰值" : "Peak", type: "scatter", data: peakIndex >= 0 ? [[peakIndex, asNumber(visual.peak_cases) ?? Number(cases[peakIndex] || 0)]] : [], symbol: "diamond", symbolSize: 13, itemStyle: { color: "#b45309" } },
    ],
  };
}

function riskMatrixOption(figureData: RecordValue, lang: "en" | "zh"): echarts.EChartsCoreOption | null {
  const rows = asArray(figureData.attention_ranking || figureData.risk_ranking).map(asRecord).slice(0, 12);
  if (rows.length < 2) return null;
  const maxScore = rows.reduce((max, row) => Math.max(max, Number(row.attention_score ?? row.risk_score ?? 0)), 1);
  return {
    animation: false,
    tooltip: {
      trigger: "item",
      confine: true,
      formatter: (params: { data?: unknown }) => {
        const item = asRecord(params.data);
        const value = Array.isArray(item.value) ? item.value : [];
        return `${asString(item.name) || "Unknown"}<br/>Latest cases: ${formatNumber(value[0])}<br/>Change: ${formatNumber(value[1])}%<br/>${lang === "zh" ? "监测关注分" : "Attention score"}: ${formatNumber(item.attention_score)}`;
      },
    },
    aria: { enabled: true },
    grid: { left: 68, right: 28, top: 30, bottom: 64 },
    xAxis: { type: "value", name: lang === "zh" ? "最新病例" : "Latest cases", min: 0, axisLabel: { color: "#5d6978" }, splitLine: { lineStyle: { color: "#e7ebf0", type: "dashed" } } },
    yAxis: { type: "value", name: lang === "zh" ? "较上一期变化(%)" : "Change vs previous (%)", axisLabel: { color: "#5d6978" }, splitLine: { lineStyle: { color: "#e7ebf0", type: "dashed" } } },
    series: [{
      name: lang === "zh" ? "疾病" : "Disease",
      type: "scatter",
      data: rows.map((row) => ({ name: asString(row.name) || "Unknown", value: [Number(row.latest_cases || 0), Number(row.change_pct || 0)], attention_score: Number(row.attention_score ?? row.risk_score ?? 0), itemStyle: { color: riskColor(row.attention_level || row.risk_level) } })),
      symbolSize: (_value: unknown, params: { data?: unknown }) => 10 + (Number(asRecord(params.data).attention_score || 0) / maxScore) * 28,
      label: { show: true, formatter: (params: { data?: unknown }) => asString(asRecord(params.data).name), color: "#263647" },
    }],
  };
}

function buildOption(figure: RecordValue, figureData: RecordValue, lang: "en" | "zh"): echarts.EChartsCoreOption | null {
  const type = asString(figure.figure_type);
  if (type === "epidemic_curve") return epidemicCurveOption(figure, figureData, lang);
  if (type === "signal_context_panel") return signalContextPanelOption(figure, figureData, lang);
  if (type === "cases_incidence_panel") return casesIncidencePanelOption(figure, figureData, lang);
  if (type === "recent_window_heatmap") return recentWindowHeatmapOption(figure, figureData, lang);
  if (type === "risk_ranking_bar") return riskRankingBarOption(figureData, lang);
  if (type === "seasonal_baseline_band") return seasonalBaselineBandOption(figure, figureData, lang);
  if (type === "anomaly_marker_curve") return anomalyMarkerCurveOption(figure, figureData, lang);
  if (type === "risk_matrix") return riskMatrixOption(figureData, lang);
  return null;
}

function figureHeight(figure: RecordValue): number {
  const explicit = asNumber(figure.height);
  if (explicit !== null) return explicit;
  const type = asString(figure.figure_type);
  if (type === "cases_incidence_panel") return 500;
  if (type === "signal_context_panel") return 330;
  if (type === "recent_window_heatmap") return 320;
  if (type === "risk_matrix") return 420;
  if (type === "seasonal_baseline_band") return 440;
  return 430;
}

function figureTypeLabel(figure: RecordValue, lang: "en" | "zh"): string {
  const type = asString(figure.figure_type);
  if (type === "risk_ranking_bar") return lang === "zh" ? "监测关注排序" : "attention ranking";
  if (type === "risk_matrix") return lang === "zh" ? "监测关注矩阵" : "attention matrix";
  return type.replaceAll("_", " ");
}

export function ReportFigureList({ figures, figureData, lang, placement }: ReportFigureListProps) {
  const visibleFigures = figures
    .filter((figure) => asString(figure.renderer) === "echarts")
    .filter((figure) => (placement ? (asString(figure.position) || "after_content") === placement : true))
    .map((figure) => ({ figure, option: buildOption(figure, figureData, lang) }))
    .filter((item): item is { figure: RecordValue; option: echarts.EChartsCoreOption } => Boolean(item.option));

  if (visibleFigures.length === 0) return null;

  return (
    <div className="not-prose my-4 space-y-3">
      {visibleFigures.map(({ figure, option }, index) => {
        const title = asString(figure.title) || (lang === "zh" ? "证据图" : "Evidence figure");
        const caption = asString(figure.caption);
        const legend = asArray(figure.legend).map(String).filter(Boolean);
        const refs = asArray(figure.evidence_refs).map(String).filter(Boolean);
        const displayNumber = figure.display_number ?? figure.number;
        const number = displayNumber == null ? "" : String(displayNumber);
        return (
          <figure
            key={asString(figure.id) || `${asString(figure.figure_type)}-${index}`}
            className="overflow-hidden rounded-tremor-default border border-tremor-border bg-tremor-background dark:border-dark-tremor-border dark:bg-dark-tremor-background"
          >
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-tremor-border px-4 py-3 dark:border-dark-tremor-border">
              <div>
                <p className="text-[11px] font-semibold uppercase tracking-wide text-tremor-brand dark:text-dark-tremor-brand">
                  {number ? `Figure ${number}` : lang === "zh" ? "图" : "Figure"}
                </p>
                <h4 className="mt-1 text-sm font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                  {title}
                </h4>
              </div>
              <span className="rounded-tremor-small bg-tremor-background-subtle px-2 py-1 text-[11px] font-medium uppercase text-tremor-content-subtle dark:bg-dark-tremor-background-subtle dark:text-dark-tremor-content-subtle">
                {figureTypeLabel(figure, lang)}
              </span>
            </div>
            <div className="px-3 py-3">
              <Chart option={option} height={figureHeight(figure)} />
            </div>
            {(caption || legend.length > 0 || refs.length > 0) && (
              <figcaption className="border-t border-tremor-border px-4 py-3 text-xs leading-5 text-tremor-content dark:border-dark-tremor-border dark:text-dark-tremor-content">
                {caption ? <p>{caption}</p> : null}
                {legend.length > 0 ? (
                  <ul className="mt-2 list-disc space-y-1 pl-4 text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {legend.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                ) : null}
                {refs.length > 0 ? (
                  <p className="mt-2 break-words text-[11px] text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                    {lang === "zh" ? "证据引用：" : "Evidence refs: "}
                    {refs.slice(0, 12).join(", ")}
                  </p>
                ) : null}
              </figcaption>
            )}
          </figure>
        );
      })}
    </div>
  );
}
