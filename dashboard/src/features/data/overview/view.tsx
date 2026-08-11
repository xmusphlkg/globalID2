"use client";

import { useMemo, useState } from "react";
import { Activity, CalendarDays, Database, Download, FileText, Maximize2, Percent, RotateCcw, Skull, TrendingUp, X } from "lucide-react";

import { Chart, echarts, type ChartExportHandle } from "@/components/charts/Chart";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterToolbar } from "@/components/ui/FilterToolbar";
import { MetricTile } from "@/components/ui/MetricTile";
import { PageHeader } from "@/components/ui/PageHeader";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { CHART_TOKENS } from "@/lib/chart-theme";
import { useDiseases, useOverviewMonthlyComparison, useOverviewSummary, useOverviewTrend } from "@/features/data/api";
import { type ReportListItem, useReports } from "@/features/reports/api";
import { t } from "@/lib/i18n";
import { formatDate, formatNumber } from "@/lib/utils";
import { useAppStore } from "@/stores/app-store";

const intervals = [
  { labelKey: "interval_30d", value: 30 },
  { labelKey: "interval_90d", value: 90 },
  { labelKey: "interval_1y", value: 365 },
  { labelKey: "interval_all", value: null },
] as const;

type TrendMetric = "cases" | "deaths" | "incidence_rate";
type TrendChart = ChartExportHandle;

const trendMetricTones: Record<TrendMetric, { color: string; soft: string; icon: typeof Activity }> = {
  cases: { color: CHART_TOKENS.warning, soft: CHART_TOKENS.warningSoft, icon: Activity },
  deaths: { color: CHART_TOKENS.destructive, soft: CHART_TOKENS.destructiveSoft, icon: Skull },
  incidence_rate: { color: CHART_TOKENS.info, soft: CHART_TOKENS.infoSoft, icon: Percent },
};

function formatMetricValue(value: number | null | undefined, metric: TrendMetric): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  if (metric === "incidence_rate") return value.toFixed(2);
  return formatNumber(Math.round(value));
}

function metricNumberFromChartValue(value: unknown): number | null {
  const raw = Array.isArray(value) ? value[1] : value;
  const numeric = typeof raw === "number" ? raw : Number(raw);
  return Number.isFinite(numeric) ? numeric : null;
}

function dateRangeLabel(startDate: string, endDate: string, fallbackStart?: string, fallbackEnd?: string, lang: "en" | "zh" = "en") {
  const start = startDate || fallbackStart;
  const end = endDate || fallbackEnd;
  if (start && end) return `${start} -> ${end}`;
  if (start) return lang === "zh" ? `${start} 之后` : `Since ${start}`;
  if (end) return lang === "zh" ? `${end} 之前` : `Until ${end}`;
  return lang === "zh" ? "全部时间" : "All time";
}

const qrVersion = 5;
const qrSize = qrVersion * 4 + 17;
const qrDataCodewords = 108;
const qrEccCodewords = 26;
const qrByteCapacity = 106;
const qrFormatBitsLowMask0 = 0b111011111000100;

function appendBits(bits: number[], value: number, length: number) {
  for (let index = length - 1; index >= 0; index -= 1) {
    bits.push((value >>> index) & 1);
  }
}

function gfMultiply(left: number, right: number): number {
  let x = left;
  let y = right;
  let result = 0;
  while (y > 0) {
    if ((y & 1) !== 0) result ^= x;
    x <<= 1;
    if ((x & 0x100) !== 0) x ^= 0x11d;
    y >>>= 1;
  }
  return result & 0xff;
}

function reedSolomonDivisor(degree: number): number[] {
  const result = Array.from({ length: degree }, () => 0);
  result[degree - 1] = 1;
  let root = 1;
  for (let index = 0; index < degree; index += 1) {
    for (let coef = 0; coef < degree; coef += 1) {
      result[coef] = gfMultiply(result[coef], root);
      if (coef + 1 < degree) result[coef] ^= result[coef + 1];
    }
    root = gfMultiply(root, 0x02);
  }
  return result;
}

function reedSolomonRemainder(data: number[], degree: number): number[] {
  const divisor = reedSolomonDivisor(degree);
  const result = Array.from({ length: degree }, () => 0);
  for (const byte of data) {
    const factor = byte ^ result.shift()!;
    result.push(0);
    for (let index = 0; index < degree; index += 1) {
      result[index] ^= gfMultiply(divisor[index], factor);
    }
  }
  return result;
}

function qrPayloadBytes(text: string): Uint8Array {
  const encoder = new TextEncoder();
  let payload = encoder.encode(text);
  if (payload.length <= qrByteCapacity) return payload;

  try {
    const url = new URL(text);
    payload = encoder.encode(`${url.origin}${url.pathname}`);
  } catch {
    payload = encoder.encode(text.slice(0, qrByteCapacity));
  }
  return payload.length <= qrByteCapacity ? payload : payload.slice(0, qrByteCapacity);
}

function qrCodewords(text: string): number[] {
  const payload = qrPayloadBytes(text);
  const bits: number[] = [];
  appendBits(bits, 0x4, 4);
  appendBits(bits, payload.length, 8);
  for (const byte of payload) appendBits(bits, byte, 8);

  const capacityBits = qrDataCodewords * 8;
  appendBits(bits, 0, Math.min(4, capacityBits - bits.length));
  while (bits.length % 8 !== 0) bits.push(0);

  const data: number[] = [];
  for (let index = 0; index < bits.length; index += 8) {
    data.push(bits.slice(index, index + 8).reduce((sum, bit) => (sum << 1) | bit, 0));
  }
  for (let pad = 0xec; data.length < qrDataCodewords; pad = pad === 0xec ? 0x11 : 0xec) {
    data.push(pad);
  }
  return [...data, ...reedSolomonRemainder(data, qrEccCodewords)];
}

function setQrFunction(modules: boolean[][], reserved: boolean[][], row: number, col: number, dark: boolean) {
  modules[row][col] = dark;
  reserved[row][col] = true;
}

function drawQrFinder(modules: boolean[][], reserved: boolean[][], row: number, col: number) {
  for (let y = -1; y <= 7; y += 1) {
    for (let x = -1; x <= 7; x += 1) {
      const currentRow = row + y;
      const currentCol = col + x;
      if (currentRow < 0 || currentRow >= qrSize || currentCol < 0 || currentCol >= qrSize) continue;
      const inFinder = x >= 0 && x <= 6 && y >= 0 && y <= 6;
      const dark = inFinder && (x === 0 || x === 6 || y === 0 || y === 6 || (x >= 2 && x <= 4 && y >= 2 && y <= 4));
      setQrFunction(modules, reserved, currentRow, currentCol, dark);
    }
  }
}

function drawQrAlignment(modules: boolean[][], reserved: boolean[][], row: number, col: number) {
  for (let y = -2; y <= 2; y += 1) {
    for (let x = -2; x <= 2; x += 1) {
      const distance = Math.max(Math.abs(x), Math.abs(y));
      setQrFunction(modules, reserved, row + y, col + x, distance !== 1);
    }
  }
}

function drawQrFunctionPatterns(modules: boolean[][], reserved: boolean[][]) {
  drawQrFinder(modules, reserved, 0, 0);
  drawQrFinder(modules, reserved, 0, qrSize - 7);
  drawQrFinder(modules, reserved, qrSize - 7, 0);
  drawQrAlignment(modules, reserved, 30, 30);

  for (let index = 8; index < qrSize - 8; index += 1) {
    setQrFunction(modules, reserved, 6, index, index % 2 === 0);
    setQrFunction(modules, reserved, index, 6, index % 2 === 0);
  }
  setQrFunction(modules, reserved, qrSize - 8, 8, true);

  for (let index = 0; index <= 5; index += 1) setQrFunction(modules, reserved, 8, index, ((qrFormatBitsLowMask0 >>> index) & 1) !== 0);
  setQrFunction(modules, reserved, 8, 7, ((qrFormatBitsLowMask0 >>> 6) & 1) !== 0);
  setQrFunction(modules, reserved, 8, 8, ((qrFormatBitsLowMask0 >>> 7) & 1) !== 0);
  setQrFunction(modules, reserved, 7, 8, ((qrFormatBitsLowMask0 >>> 8) & 1) !== 0);
  for (let index = 9; index < 15; index += 1) setQrFunction(modules, reserved, 14 - index, 8, ((qrFormatBitsLowMask0 >>> index) & 1) !== 0);
  for (let index = 0; index < 8; index += 1) setQrFunction(modules, reserved, qrSize - 1 - index, 8, ((qrFormatBitsLowMask0 >>> index) & 1) !== 0);
  for (let index = 8; index < 15; index += 1) setQrFunction(modules, reserved, 8, qrSize - 15 + index, ((qrFormatBitsLowMask0 >>> index) & 1) !== 0);
}

function createQrDataUrl(text: string, size = 148): string {
  const modules = Array.from({ length: qrSize }, () => Array.from({ length: qrSize }, () => false));
  const reserved = Array.from({ length: qrSize }, () => Array.from({ length: qrSize }, () => false));
  drawQrFunctionPatterns(modules, reserved);

  const bits = qrCodewords(text).flatMap((byte) => Array.from({ length: 8 }, (_, index) => (byte >>> (7 - index)) & 1));
  let bitIndex = 0;
  let upward = true;
  for (let right = qrSize - 1; right >= 1; right -= 2) {
    if (right === 6) right -= 1;
    for (let vertical = 0; vertical < qrSize; vertical += 1) {
      const row = upward ? qrSize - 1 - vertical : vertical;
      for (let offset = 0; offset < 2; offset += 1) {
        const col = right - offset;
        if (reserved[row][col]) continue;
        const masked = (bits[bitIndex] ?? 0) ^ (((row + col) % 2 === 0) ? 1 : 0);
        modules[row][col] = masked === 1;
        bitIndex += 1;
      }
    }
    upward = !upward;
  }

  const viewSize = qrSize + 8;
  const rects = modules
    .flatMap((row, y) => row.map((filled, x) => (filled ? `<rect x="${x + 4}" y="${y + 4}" width="1" height="1"/>` : "")))
    .join("");
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${viewSize} ${viewSize}" shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fff"/><g fill="#17211f">${rects}</g></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = reject;
    image.src = src;
  });
}

function fitRect(sourceWidth: number, sourceHeight: number, targetWidth: number, targetHeight: number) {
  const ratio = Math.min(targetWidth / sourceWidth, targetHeight / sourceHeight);
  return {
    width: sourceWidth * ratio,
    height: sourceHeight * ratio,
  };
}

function downloadCanvas(canvas: HTMLCanvasElement, filename: string) {
  const link = document.createElement("a");
  link.href = canvas.toDataURL("image/png");
  link.download = filename;
  link.click();
}

export default function DataDashboardPage() {
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

  const downloadDashboardChartImage = async (
    chart: TrendChart | null,
    title: string,
    subtitle: string,
    filenamePrefix: string,
  ) => {
    if (!chart || typeof document === "undefined") return;
    const chartUrl = chart.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: "#ffffff" });
    const pageUrl = typeof window === "undefined" ? "GIDS Dashboard" : window.location.href;
    const [chartImage, qrImage] = await Promise.all([loadImage(chartUrl), loadImage(createQrDataUrl(pageUrl))]);

    const canvas = document.createElement("canvas");
    canvas.width = 1600;
    canvas.height = 1120;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    ctx.fillStyle = "#17211f";
    ctx.font = "700 34px Arial, sans-serif";
    ctx.fillText(title, 56, 62);
    ctx.font = "500 22px Arial, sans-serif";
    ctx.fillStyle = "#4f5f5a";
    ctx.fillText(subtitle, 56, 98);

    const footerHeight = 184;
    const chartArea = fitRect(chartImage.width, chartImage.height, canvas.width - 112, canvas.height - footerHeight - 140);
    const chartX = (canvas.width - chartArea.width) / 2;
    const chartY = 122;
    ctx.drawImage(chartImage, chartX, chartY, chartArea.width, chartArea.height);

    const footerY = canvas.height - footerHeight;
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, footerY, canvas.width, footerHeight);
    ctx.strokeStyle = "#d7ddd7";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(0, footerY);
    ctx.lineTo(canvas.width, footerY);
    ctx.stroke();

    ctx.drawImage(qrImage, 56, footerY + 26, 132, 132);
    ctx.fillStyle = "#17211f";
    ctx.font = "700 22px Arial, sans-serif";
    ctx.fillText("GIDS Dashboard", 220, footerY + 56);
    ctx.font = "400 18px Arial, sans-serif";
    ctx.fillStyle = "#4f5f5a";
    ctx.fillText(pageUrl.length > 82 ? `${pageUrl.slice(0, 79)}...` : pageUrl, 220, footerY + 88);
    ctx.fillText(lang === "zh" ? "扫码打开当前仪表盘视图" : "Scan to open this dashboard view", 220, footerY + 120);

    const exportedAt = new Date().toLocaleString(lang === "zh" ? "zh-CN" : "en-US");
    const detailX = 940;
    ctx.fillStyle = "#17211f";
    ctx.font = "700 20px Arial, sans-serif";
    ctx.fillText(lang === "zh" ? "导出信息" : "Export Details", detailX, footerY + 56);
    ctx.font = "400 18px Arial, sans-serif";
    ctx.fillStyle = "#4f5f5a";
    ctx.fillText(`${t(lang, "period")}: ${currentDateRangeLabel}`, detailX, footerY + 88);
    ctx.fillText(`${lang === "zh" ? "指标" : "Metric"}: ${activeMetricLabel}`, detailX, footerY + 118);
    ctx.fillText(`${lang === "zh" ? "导出时间" : "Exported"}: ${exportedAt}`, detailX, footerY + 148);

    const safeMetric = trendMetric.replace(/_/g, "-");
    downloadCanvas(canvas, `${filenamePrefix}-${safeMetric}-${new Date().toISOString().slice(0, 10)}.png`);
  };

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
        title={lang === "zh" ? t(lang, "dashboard") : "Country Analytics"}
        description={
          lang === "zh"
            ? "按当前国家汇总疾病指标、趋势和发布流转状态。"
            : "Country-level disease metrics, trend analysis, and release pipeline snapshot."
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

      <FilterToolbar>
        <select
          aria-label={lang === "zh" ? "疾病筛选" : "Disease filter"}
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
          aria-label={lang === "zh" ? "时间范围" : "Date range"}
          value={interval ? String(interval) : "all"}
          onChange={(event) => {
            setInterval(event.target.value === "all" ? null : Number(event.target.value));
            setStartDate("");
            setEndDate("");
          }}
          className="h-10 min-w-[150px] rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm text-tremor-content-strong outline-none transition focus:border-tremor-brand-subtle focus:ring-2 focus:ring-tremor-brand-muted dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
        >
          {intervals.map((item) => (
            <option key={item.labelKey} value={item.value ? String(item.value) : "all"}>
              {t(lang, item.labelKey)}
            </option>
          ))}
        </select>

        <div className="flex h-10 items-center gap-1 rounded-tremor-default border border-tremor-border bg-tremor-background-subtle p-1 dark:border-dark-tremor-border dark:bg-dark-tremor-background-subtle">
          {metricButtons.map((item) => {
            const Icon = item.icon;
            const active = trendMetric === item.value;
            return (
              <button
                key={item.value}
                type="button"
                onClick={() => setTrendMetric(item.value)}
                className={`inline-flex h-8 items-center gap-1.5 rounded-tremor-default px-2.5 text-sm font-medium transition ${
                  active
                    ? "bg-tremor-background text-tremor-content-strong shadow-[0_1px_2px_rgba(23,33,31,0.08)] dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong"
                    : "text-tremor-content-subtle hover:text-tremor-content-strong dark:text-dark-tremor-content-subtle dark:hover:text-dark-tremor-content-strong"
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                {item.label}
              </button>
            );
          })}
        </div>

        <div className="flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-2 dark:border-dark-tremor-border dark:bg-dark-tremor-background">
          <CalendarDays className="h-4 w-4 text-tremor-content-subtle dark:text-dark-tremor-content-subtle" />
          <input
            type="date"
            value={startDate}
            min={summary?.earliest_date ?? undefined}
            max={endDate || summary?.latest_date || undefined}
            onChange={(event) => {
              setStartDate(event.target.value);
              setInterval(null);
            }}
            className="h-8 w-[135px] border-none bg-transparent p-0 text-sm text-tremor-content-strong outline-none dark:text-dark-tremor-content-strong"
            aria-label={lang === "zh" ? "开始日期" : "Start date"}
          />
          <span className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">to</span>
          <input
            type="date"
            value={endDate}
            min={startDate || summary?.earliest_date || undefined}
            max={summary?.latest_date ?? undefined}
            onChange={(event) => {
              setEndDate(event.target.value);
              setInterval(null);
            }}
            className="h-8 w-[135px] border-none bg-transparent p-0 text-sm text-tremor-content-strong outline-none dark:text-dark-tremor-content-strong"
            aria-label={lang === "zh" ? "结束日期" : "End date"}
          />
        </div>

        {hasCustomDateRange ? (
          <button
            type="button"
            onClick={() => {
              setStartDate("");
              setEndDate("");
            }}
            className="inline-flex h-10 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-medium text-tremor-content transition hover:bg-tremor-background-subtle dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content dark:hover:bg-dark-tremor-background-subtle"
          >
            <RotateCcw className="h-4 w-4" />
            {lang === "zh" ? "清除日期" : "Clear dates"}
          </button>
        ) : null}
      </FilterToolbar>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <section className="app-panel p-4">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {t(lang, "trend")}
              </h2>
              <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {focusLabel} · {activeMetricLabel} · {currentDateRangeLabel}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <StatusBadge tone="info">{trend?.length ?? 0} points</StatusBadge>
              <button
                type="button"
                onClick={() => setIsTrendFullscreen(true)}
                disabled={!trendOption}
                className="icon-button disabled:cursor-not-allowed disabled:opacity-50"
                aria-label={lang === "zh" ? "全屏查看" : "View fullscreen"}
                title={lang === "zh" ? "全屏查看" : "View fullscreen"}
              >
                <Maximize2 className="h-4 w-4" />
              </button>
            </div>
          </div>

          {trendOption ? (
            <Chart
              height={330}
              option={trendOption}
              onReady={setTrendChart}
            />
          ) : (
            <EmptyState
              title={isLoading ? t(lang, "loading") : t(lang, "no_data")}
              className="h-[330px] rounded-tremor-default border border-dashed border-tremor-border dark:border-dark-tremor-border"
            />
          )}
        </section>

        <aside className="space-y-4">
          <section className="app-panel p-4">
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

          <section className="app-panel p-4">
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

      <section className="app-panel p-4">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
              {lang === "zh" ? "逐年月度对比" : "Yearly Monthly Comparison"}
            </h2>
            <p className="text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
              {focusLabel} · {activeMetricLabel} · {currentDateRangeLabel}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <StatusBadge tone="info">
              {monthlyComparison ? `${new Set(monthlyComparison.map((row) => row.year)).size} years` : "0 years"}
            </StatusBadge>
            <button
              type="button"
              onClick={() => setIsMonthlyFullscreen(true)}
              disabled={!monthlyComparisonOption}
              className="icon-button disabled:cursor-not-allowed disabled:opacity-50"
              aria-label={lang === "zh" ? "全屏查看" : "View fullscreen"}
              title={lang === "zh" ? "全屏查看" : "View fullscreen"}
            >
              <Maximize2 className="h-4 w-4" />
            </button>
          </div>
        </div>

        {monthlyComparisonOption ? (
          <Chart
            height={360}
            option={monthlyComparisonOption}
            onReady={setMonthlyChart}
          />
        ) : (
          <EmptyState
            title={isLoading ? t(lang, "loading") : t(lang, "no_data")}
            className="h-[360px] rounded-tremor-default border border-dashed border-tremor-border dark:border-dark-tremor-border"
          />
        )}
      </section>

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

      {isTrendFullscreen ? (
        <div className="fixed inset-0 z-50 flex flex-col bg-tremor-background dark:bg-dark-tremor-background">
          <div className="flex min-h-16 flex-wrap items-center justify-between gap-3 border-b border-tremor-border px-5 py-3 dark:border-dark-tremor-border">
            <div className="min-w-0">
              <h2 className="truncate text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {t(lang, "trend")}
              </h2>
              <p className="truncate text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {focusLabel} · {activeMetricLabel} · {currentDateRangeLabel}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() =>
                  downloadDashboardChartImage(
                    fullscreenTrendChart ?? trendChart,
                    trendExportTitle,
                    chartSubtitle,
                    "gids-epidemic-trend",
                  )
                }
                disabled={!trendOption}
                className="inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-semibold text-tremor-content-strong transition hover:bg-tremor-background-subtle disabled:cursor-not-allowed disabled:opacity-50 dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
              >
                <Download className="h-4 w-4" />
                {lang === "zh" ? "下载图片" : "Download image"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setIsTrendFullscreen(false);
                  setFullscreenTrendChart(null);
                }}
                className="icon-button"
                aria-label={lang === "zh" ? "关闭全屏" : "Close fullscreen"}
                title={lang === "zh" ? "关闭全屏" : "Close fullscreen"}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
          <div className="min-h-0 flex-1 p-4">
            {trendOption ? (
              <Chart
                className="h-full"
                height="calc(100vh - 112px)"
                option={trendOption}
                onReady={setFullscreenTrendChart}
              />
            ) : (
              <EmptyState
                title={isLoading ? t(lang, "loading") : t(lang, "no_data")}
                className="h-full rounded-tremor-default border border-dashed border-tremor-border dark:border-dark-tremor-border"
              />
            )}
          </div>
        </div>
      ) : null}

      {isMonthlyFullscreen ? (
        <div className="fixed inset-0 z-50 flex flex-col bg-tremor-background dark:bg-dark-tremor-background">
          <div className="flex min-h-16 flex-wrap items-center justify-between gap-3 border-b border-tremor-border px-5 py-3 dark:border-dark-tremor-border">
            <div className="min-w-0">
              <h2 className="truncate text-base font-semibold text-tremor-content-strong dark:text-dark-tremor-content-strong">
                {lang === "zh" ? "逐年月度对比" : "Yearly Monthly Comparison"}
              </h2>
              <p className="truncate text-xs text-tremor-content-subtle dark:text-dark-tremor-content-subtle">
                {focusLabel} · {activeMetricLabel} · {currentDateRangeLabel}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() =>
                  downloadDashboardChartImage(
                    fullscreenMonthlyChart ?? monthlyChart,
                    monthlyExportTitle,
                    chartSubtitle,
                    "gids-yearly-monthly-comparison",
                  )
                }
                disabled={!monthlyComparisonOption}
                className="inline-flex h-9 items-center gap-2 rounded-tremor-default border border-tremor-border bg-tremor-background px-3 text-sm font-semibold text-tremor-content-strong transition hover:bg-tremor-background-subtle disabled:cursor-not-allowed disabled:opacity-50 dark:border-dark-tremor-border dark:bg-dark-tremor-background dark:text-dark-tremor-content-strong dark:hover:bg-dark-tremor-background-subtle"
              >
                <Download className="h-4 w-4" />
                {lang === "zh" ? "下载图片" : "Download image"}
              </button>
              <button
                type="button"
                onClick={() => {
                  setIsMonthlyFullscreen(false);
                  setFullscreenMonthlyChart(null);
                }}
                className="icon-button"
                aria-label={lang === "zh" ? "关闭全屏" : "Close fullscreen"}
                title={lang === "zh" ? "关闭全屏" : "Close fullscreen"}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
          <div className="min-h-0 flex-1 p-4">
            {monthlyComparisonOption ? (
              <Chart
                className="h-full"
                height="calc(100vh - 112px)"
                option={monthlyComparisonOption}
                onReady={setFullscreenMonthlyChart}
              />
            ) : (
              <EmptyState
                title={isLoading ? t(lang, "loading") : t(lang, "no_data")}
                className="h-full rounded-tremor-default border border-dashed border-tremor-border dark:border-dark-tremor-border"
              />
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
