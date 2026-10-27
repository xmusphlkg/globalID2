import { useEffect, useMemo, useRef } from "react";
import EChartsReact from "../../../../lib/echartsReact";
import echarts from "../../../../lib/echarts";
import { useChartTheme } from "../../chartPreferences";
import { DAY, periodFor } from "../domain/calendar";
import {
  fingerprint,
  type AnalysisResult,
  type Catalog,
  type DateWindow,
  type Language,
  type ResultLine,
} from "../domain/types";
import { label, lineName, number } from "../presentation/labels";

const PALETTE = [
  "#0072b2",
  "#d55e00",
  "#009e73",
  "#cc79a7",
  "#e69f00",
  "#56b4e9",
  "#7f3c8d",
  "#777777",
  "#bc5090",
  "#4e79a7",
];
export function seriesColor(id: string) {
  return PALETTE[parseInt(fingerprint(id), 36) % PALETTE.length];
}
const escape = (value: string) =>
  value.replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ]!,
  );
function withGaps(line: ResultLine) {
  const data: Array<[string, number | null]> = [];
  line.points.forEach((point, index) => {
    const previous = line.points[index - 1];
    if (previous) {
      const end = periodFor(previous.date, line.cadence).endExclusive;
      if (end && point.date > end) data.push([end, null]);
      else if (
        line.cadence === "weekly" &&
        Date.parse(point.date) - Date.parse(previous.date) > 8 * DAY
      )
        data.push([
          new Date(Date.parse(previous.date) + 7 * DAY)
            .toISOString()
            .slice(0, 10),
          null,
        ]);
    }
    data.push([point.date, point.value]);
  });
  return data;
}
interface Props {
  result: AnalysisResult;
  catalog: Catalog;
  lang: Language;
  panelPage: number;
  onWindow: (window: DateWindow) => void;
}
export default function SurveillancePlot({
  result,
  catalog,
  lang,
  panelPage,
  onWindow,
}: Props) {
  const theme = useChartTheme();
  const ref = useRef<any>(null);
  const plotRef = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  useEffect(() => {
    const plot = plotRef.current;
    if (!plot) return;
    let frame = 0;
    const resize = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => ref.current?.getEchartsInstance?.()?.resize());
    };
    const observer = new ResizeObserver(resize);
    observer.observe(plot);
    document.addEventListener("fullscreenchange", resize);
    return () => {
      observer.disconnect();
      document.removeEventListener("fullscreenchange", resize);
      cancelAnimationFrame(frame);
    };
  }, []);
  const panels = [...new Set(result.lines.map((line) => line.panelId))];
  const visiblePanels = panels.slice(panelPage * 4, panelPage * 4 + 4);
  const visible = result.lines.filter((line) => visiblePanels.includes(line.panelId));
  const height = Math.max(360, visiblePanels.length * 205 + 90);
  const option = useMemo(() => {
    const dark = theme === "dark";
    const ink = dark ? "#a4b1c1" : "#556070",
      grid = dark ? "#344a64" : "#d9d2c7";
    const count = Math.max(1, visiblePanels.length);
    const slot = (height - 85) / count;
    const gridIndexes = visiblePanels.map((_, index) => index);
    const series = visible.flatMap((line) => {
      const panelIndex = visiblePanels.indexOf(line.panelId);
      const color = seriesColor(line.id);
      const data = withGaps(line);
      const main = {
        id: line.id,
        name: lineName(line, catalog, lang),
        type: result.config.task === "outbreak" ? "bar" : "line",
        xAxisIndex: panelIndex,
        yAxisIndex: panelIndex,
        data,
        symbolSize: 5,
        showSymbol: data.length <= 60,
        connectNulls: false,
        lineStyle: {
          width: 2,
          type: catalog.selections[line.selectionId]?.isDefault
            ? "solid"
            : "dashed",
        },
        itemStyle: { color },
        barMaxWidth: 24,
        markLine: {
          silent: true,
          symbol: "none",
          label: { show: false },
          lineStyle: { color: ink, type: "dotted" },
          data: [
            ...line.events.map((event) => ({
              xAxis: event.date,
              name: label(event.label, lang),
            })),
            ...(result.config.task === "historical" ? [{ yAxis: 100 }] : []),
            ...(result.config.task === "change" ? [{ yAxis: 0 }] : []),
          ],
        },
      };
      if (
        result.config.task !== "trend" ||
        !line.points.some((point) => point.expected != null)
      )
        return [main];
      const refBase = {
        type: "line",
        xAxisIndex: panelIndex,
        yAxisIndex: panelIndex,
        symbol: "none",
        silent: true,
        connectNulls: false,
        lineStyle: { opacity: 0 },
        tooltip: { show: false },
      };
      return [
        {
          ...refBase,
          id: `${line.id}/lower`,
          name: "__reference_lower",
          stack: `${line.id}/reference`,
          stackStrategy: "all",
          data: line.points.map((point) => [point.date, point.lower ?? null]),
        },
        {
          ...refBase,
          id: `${line.id}/range`,
          name: "__reference_range",
          stack: `${line.id}/reference`,
          stackStrategy: "all",
          areaStyle: { color, opacity: 0.12 },
          data: line.points.map((point) => [
            point.date,
            point.upper != null && point.lower != null
              ? point.upper - point.lower
              : null,
          ]),
        },
        {
          ...refBase,
          id: `${line.id}/median`,
          name: "__reference_median",
          lineStyle: { color, type: "dotted", opacity: 0.6 },
          data: line.points.map((point) => [
            point.date,
            point.expected ?? null,
          ]),
        },
        main,
      ];
    });
    return {
      animation:
        visible.reduce((sum, line) => sum + line.points.length, 0) < 5000,
      animationDuration: 120,
      aria: { enabled: true },
      grid: visiblePanels.map((_, index) => ({
        left: 72,
        right: 24,
        top: index * slot + 45,
        height: slot - 70,
      })),
      title: visiblePanels.map((panel, index) => {
        const lines = result.lines.filter((line) => line.panelId === panel);
        const first = lines[0];
        return {
          text: `${count > 1 || result.layout === "facets" ? `${catalog.entities[first.entityId]?.labels[lang] ?? ""} · ` : ""}${label(first.unit, lang)} · ${label(first.displayCadence ?? first.cadence, lang)}${first.displayCadence && first.displayCadence !== first.cadence ? "*" : ""}`,
          left: 12,
          top: index * slot + 10,
          textStyle: {
            color: ink,
            fontSize: 12,
            fontWeight: 500,
            width: 500,
            overflow: "truncate",
          },
        };
      }),
      xAxis: visiblePanels.map((_, index) => ({
        type: "time",
        gridIndex: index,
        min: result.window?.startDate,
        max: result.window?.endDate,
        axisLabel: {
          color: ink,
          hideOverlap: true,
          ...(result.config.task === "seasonality"
            ? { formatter: "{MM}" }
            : {}),
        },
        axisLine: { lineStyle: { color: grid } },
        splitLine: { show: false },
      })),
      yAxis: visiblePanels.map((_, index) => ({
        type: result.scale === "log" ? "log" : "value",
        gridIndex: index,
        min:
          result.scale === "log"
            ? undefined
            : (extent: { min: number }) => Math.min(0, extent.min),
        axisLabel: {
          color: ink,
          formatter: (v: number) =>
            Math.abs(v) >= 1_000_000
              ? `${number(v / 1_000_000, lang)}M`
              : Math.abs(v) >= 10000
                ? `${number(v / 1000, lang)}k`
                : number(v, lang),
        },
        splitLine: { lineStyle: { color: grid } },
      })),
      tooltip: {
        trigger: "axis",
        confine: true,
        backgroundColor: dark ? "#14263b" : "#ffffff",
        borderColor: grid,
        textStyle: { color: ink },
        formatter: (params: any[]) =>
          params
            .filter(
              (param) => !String(param.seriesName).startsWith("__reference"),
            )
            .map((param) => {
              const line = visible.find((item) => item.id === param.seriesId);
              const date = Array.isArray(param.value)
                ? String(param.value[0]).slice(0, 10)
                : "";
              const point = line?.points.find((item) => item.date === date);
              if (!line || !point) return "";
              return `${escape(lineName(line, catalog, lang))}<br/>${escape(point.period.label)}: <b>${number(point.value, lang)}</b> ${escape(label(line.unit, lang))}${point.comparison ? `<br/>${escape(label("baseline", lang))} (${escape(point.comparison.periodLabel)}): ${number(point.comparison.value, lang)}` : ""}${point.quality.length ? `<br/>${escape(point.quality.map((status) => label(status, lang)).join(", "))}` : ""}${point.expected != null ? `<br/>${escape(label("reference", lang))}: ${number(point.expected, lang)} (${number(point.lower, lang)}–${number(point.upper, lang)})` : ""}`;
            })
            .join("<br/><br/>"),
      },
      dataZoom:
        result.config.task === "seasonality"
          ? []
          : [
              {
                id: "surveillance-inside",
                type: "inside",
                xAxisIndex: gridIndexes,
                filterMode: "none",
                zoomOnMouseWheel: "ctrl",
                moveOnMouseWheel: false,
                moveOnMouseMove: false,
              },
              {
                id: "surveillance-slider",
                type: "slider",
                xAxisIndex: gridIndexes,
                filterMode: "none",
                bottom: 10,
                height: 24,
                left: 72,
                right: 24,
                showDataShadow: false,
                showDetail: false,
                brushSelect: true,
              },
            ],
      series,
    };
  }, [result, catalog, lang, theme, panelPage]);
  const events = useMemo(
    () => ({
      datazoom: () => {
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => {
          const chart = ref.current?.getEchartsInstance?.();
          const zoom = chart
            ?.getOption?.()
            ?.dataZoom?.find((item: any) => item.id === "surveillance-slider");
          const domain = result.window ?? result.domain;
          if (!zoom || !domain) return;
          const start = Date.parse(domain.startDate),
            end = Date.parse(domain.endDate);
          const value = (v: unknown, percent: number) => {
            const timestamp =
              typeof v === "number"
                ? v
                : typeof v === "string"
                  ? Date.parse(v)
                  : start + ((end - start) * percent) / 100;
            return Number.isFinite(timestamp)
              ? new Date(timestamp).toISOString().slice(0, 10)
              : null;
          };
          const startDate = value(zoom.startValue, zoom.start ?? 0),
            endDate = value(zoom.endValue, zoom.end ?? 100);
          if (
            startDate &&
            endDate &&
            startDate <= endDate &&
            (startDate !== domain.startDate || endDate !== domain.endDate)
          )
            onWindow({ startDate, endDate });
        }, 150);
      },
    }),
    [result.window, result.domain, onWindow],
  );
  if (!visible.length)
    return (
      <div className="surveillance-empty" role="status">
        {label(
          result.config.selectionIds.length ? "noData" : "noSelection",
          lang,
        )}
      </div>
    );
  return (
    <div
      ref={plotRef}
      className="surveillance-plot"
      data-testid="surveillance-plot"
      aria-label={label("chart", lang)}
    >
      <EChartsReact
        ref={ref}
        echarts={echarts}
        option={option}
        notMerge
        onEvents={events}
        style={{ height, width: "100%" }}
        opts={{ renderer: "canvas" }}
      />
    </div>
  );
}
