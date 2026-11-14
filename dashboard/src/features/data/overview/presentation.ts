
import { Activity, Percent, Skull } from "lucide-react";

import { type ChartExportHandle } from "@/components/charts/Chart";

import { CHART_TOKENS } from "@/lib/chart-theme";

import { formatNumber } from "@/lib/utils";

export const intervals = [
  { labelKey: "interval_30d", value: 30 },
  { labelKey: "interval_90d", value: 90 },
  { labelKey: "interval_1y", value: 365 },
  { labelKey: "interval_all", value: null },
] as const;

export type TrendMetric = "cases" | "deaths" | "incidence_rate";
export type TrendChart = ChartExportHandle;

export const trendMetricTones: Record<TrendMetric, { color: string; soft: string; icon: typeof Activity }> = {
  cases: { color: CHART_TOKENS.warning, soft: CHART_TOKENS.warningSoft, icon: Activity },
  deaths: { color: CHART_TOKENS.destructive, soft: CHART_TOKENS.destructiveSoft, icon: Skull },
  incidence_rate: { color: CHART_TOKENS.info, soft: CHART_TOKENS.infoSoft, icon: Percent },
};

export function formatMetricValue(value: number | null | undefined, metric: TrendMetric): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  if (metric === "incidence_rate") return value.toFixed(2);
  return formatNumber(Math.round(value));
}

export function metricNumberFromChartValue(value: unknown): number | null {
  const raw = Array.isArray(value) ? value[1] : value;
  if (raw === null || raw === undefined || (typeof raw === "string" && raw.trim() === "")) return null;
  const numeric = typeof raw === "number" ? raw : Number(raw);
  return Number.isFinite(numeric) ? numeric : null;
}

export function dateRangeLabel(startDate: string, endDate: string, fallbackStart?: string, fallbackEnd?: string, lang: "en" | "zh" = "en") {
  const start = startDate || fallbackStart;
  const end = endDate || fallbackEnd;
  if (start && end) return `${start} -> ${end}`;
  if (start) return lang === "zh" ? `${start} 之后` : `Since ${start}`;
  if (end) return lang === "zh" ? `${end} 之前` : `Until ${end}`;
  return lang === "zh" ? "全部时间" : "All time";
}
