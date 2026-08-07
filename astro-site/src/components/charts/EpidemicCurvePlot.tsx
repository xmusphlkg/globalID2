import { useCallback, useEffect, useMemo, useRef } from 'react';
import EChartsReact from '../../lib/echartsReact';
import echarts from '../../lib/echarts';
import {
  ANIMATION_POINT_LIMIT,
  METRIC_LABELS,
  dateWindowFromZoom,
  type DateWindow,
  type EpidemicMetric,
} from './epidemicCurveModel';

export interface EpidemicCurveLine {
  id: string;
  name: string;
  color: string;
  dates: string[];
  values: (number | null)[];
}

interface ChartColors {
  font: string;
  line: string;
  grid: string;
  hoverBg: string;
  hoverBorder: string;
  hoverFont: string;
  sliderBg: string;
  fillerColor: string;
  title: string;
}

interface Props {
  lines: EpidemicCurveLine[];
  activeDates: string[];
  dateWindow: DateWindow | null;
  onDateWindowChange: (dateWindow: DateWindow) => void;
  metric: EpidemicMetric;
  lang: 'en' | 'zh';
  colors: ChartColors;
  title?: string;
  height: number | string;
}

function formatTooltipValue(value: unknown, metric: EpidemicMetric) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) return '—';
  return metric === 'incidence_rates'
    ? numericValue.toFixed(2)
    : numericValue.toLocaleString();
}

export default function EpidemicCurvePlot({
  lines,
  activeDates,
  dateWindow,
  onDateWindowChange,
  metric,
  lang,
  colors,
  title,
  height,
}: Props) {
  const zoomCommitTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activePointCount = useMemo(
    () => lines.reduce((total, line) => total + line.values.length, 0),
    [lines]
  );
  const fullDateWindow = useMemo(() => (
    activeDates.length > 0
      ? {
          startDate: activeDates[0],
          endDate: activeDates[activeDates.length - 1],
        }
      : null
  ), [activeDates]);
  const canResetDateWindow = Boolean(
    fullDateWindow
      && dateWindow
      && (
        dateWindow.startDate !== fullDateWindow.startDate
        || dateWindow.endDate !== fullDateWindow.endDate
      )
  );

  useEffect(() => () => {
    if (zoomCommitTimer.current) clearTimeout(zoomCommitTimer.current);
  }, []);

  const handleDataZoom = useCallback((params: any, chart: any) => {
    const eventRange = Array.isArray(params?.batch) ? params.batch[0] : params;
    let start = Number(eventRange?.start);
    let end = Number(eventRange?.end);
    let startValue = eventRange?.startValue as string | number | undefined;
    let endValue = eventRange?.endValue as string | number | undefined;
    const dataZoom = chart?.getOption?.()?.dataZoom;
    const slider = Array.isArray(dataZoom)
      ? dataZoom.find((item: any) => item?.id === 'epidemic-zoom-slider') ?? dataZoom[0]
      : null;

    if (!Number.isFinite(start) || !Number.isFinite(end)) {
      start = Number(slider?.start);
      end = Number(slider?.end);
    }
    startValue ??= slider?.startValue;
    endValue ??= slider?.endValue;

    if (!Number.isFinite(start) || !Number.isFinite(end)) return;
    const nextWindow = dateWindowFromZoom(
      activeDates,
      start,
      end,
      startValue,
      endValue
    );
    if (!nextWindow) return;

    // Let ECharts own the high-frequency drag updates. Committing only after
    // the pointer settles prevents React option updates from fighting the
    // slider handles while preserving a live chart preview.
    if (zoomCommitTimer.current) clearTimeout(zoomCommitTimer.current);
    zoomCommitTimer.current = setTimeout(() => {
      onDateWindowChange(nextWindow);
      zoomCommitTimer.current = null;
    }, 120);
  }, [activeDates, onDateWindowChange]);

  const resetDateWindow = useCallback(() => {
    if (!fullDateWindow) return;
    if (zoomCommitTimer.current) {
      clearTimeout(zoomCommitTimer.current);
      zoomCommitTimer.current = null;
    }
    onDateWindowChange(fullDateWindow);
  }, [fullDateWindow, onDateWindowChange]);

  const chartEvents = useMemo(
    () => ({ datazoom: handleDataZoom }),
    [handleDataZoom]
  );

  const option = useMemo(() => ({
    animation: activePointCount <= ANIMATION_POINT_LIMIT,
    animationDuration: 180,
    backgroundColor: 'transparent',
    title: title
      ? {
          text: title,
          textStyle: {
            color: colors.title,
            fontSize: 15,
            fontWeight: 600,
          },
          left: 0,
          top: 0,
        }
      : undefined,
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        type: 'line' as const,
        lineStyle: { color: colors.line, type: 'dashed' as const },
      },
      backgroundColor: colors.hoverBg,
      borderColor: colors.hoverBorder,
      borderWidth: 1,
      textStyle: { color: colors.hoverFont, fontSize: 12 },
      formatter: (params: any[]) => {
        const date = params[0]?.axisValueLabel ?? params[0]?.axisValue ?? '';
        return [
          `<b>${date}</b>`,
          ...params.map((param) => (
            `${param.marker}${param.seriesName}: <b>${formatTooltipValue(param.value?.[1] ?? param.value, metric)}</b>`
          )),
        ].join('<br/>');
      },
    },
    grid: { left: 64, right: 18, top: title ? 38 : 16, bottom: 62 },
    xAxis: {
      type: 'time' as const,
      axisLabel: { color: colors.font, fontSize: 11 },
      axisLine: { lineStyle: { color: colors.line } },
      axisTick: { lineStyle: { color: colors.line } },
      splitLine: { lineStyle: { color: colors.grid, type: 'dashed' as const } },
    },
    yAxis: {
      type: 'value' as const,
      name: METRIC_LABELS[metric][lang],
      nameTextStyle: { color: colors.font, fontSize: 11, padding: [0, 0, 8, 0] },
      axisLabel: { color: colors.font, fontSize: 11 },
      axisLine: { lineStyle: { color: colors.line } },
      axisTick: { lineStyle: { color: colors.line } },
      splitLine: { lineStyle: { color: colors.grid, type: 'dashed' as const } },
      min: 0,
    },
    dataZoom: [
      {
        id: 'epidemic-zoom-inside',
        type: 'inside' as const,
        xAxisIndex: 0,
        filterMode: 'filter' as const,
        startValue: dateWindow?.startDate,
        endValue: dateWindow?.endDate,
        throttle: 16,
        zoomOnMouseWheel: 'ctrl',
        moveOnMouseWheel: false,
        moveOnMouseMove: true,
        preventDefaultMouseMove: true,
      },
      {
        id: 'epidemic-zoom-slider',
        type: 'slider' as const,
        xAxisIndex: 0,
        filterMode: 'filter' as const,
        startValue: dateWindow?.startDate,
        endValue: dateWindow?.endDate,
        throttle: 16,
        realtime: true,
        left: 64,
        right: 18,
        bottom: 8,
        height: 24,
        showDataShadow: false,
        showDetail: false,
        brushSelect: true,
        handleIcon: 'path://M2,0 L14,0 C15.1,0 16,0.9 16,2 L16,22 C16,23.1 15.1,24 14,24 L2,24 C0.9,24 0,23.1 0,22 L0,2 C0,0.9 0.9,0 2,0 Z',
        handleSize: '82%',
        moveHandleIcon: 'path://M0,0 L20,0 L20,4 L0,4 Z',
        moveHandleSize: 7,
        backgroundColor: 'transparent',
        borderColor: colors.line,
        textStyle: { color: colors.font, fontSize: 10 },
        fillerColor: colors.fillerColor,
        handleStyle: {
          color: colors.hoverBg,
          borderColor: colors.hoverBorder,
          borderWidth: 1.5,
          shadowBlur: 4,
          shadowColor: 'rgba(15, 23, 42, 0.18)',
        },
        moveHandleStyle: {
          color: colors.hoverBorder,
          opacity: 0.72,
        },
        emphasis: {
          handleStyle: {
            color: colors.hoverBg,
            borderColor: colors.hoverFont,
            borderWidth: 2,
          },
          moveHandleStyle: {
            color: colors.hoverFont,
            opacity: 0.9,
          },
        },
      },
    ],
    series: lines.map((line, index) => ({
      id: line.id,
      name: line.name,
      type: 'line' as const,
      showSymbol: false,
      smooth: false,
      sampling: 'lttb' as const,
      lineStyle: {
        color: line.color,
        width: 2.4,
        type: 'solid' as const,
      },
      itemStyle: { color: line.color },
      emphasis: { focus: 'series' as const },
      z: 10 + index,
      data: line.dates.map((date, valueIndex) => [
        date,
        line.values[valueIndex] ?? null,
      ]),
    })),
  }), [activePointCount, colors, dateWindow, lang, lines, metric, title]);

  return (
    <div className="epidemic-curve-plot" style={{ height }}>
      <EChartsReact
        echarts={echarts}
        option={option}
        onEvents={chartEvents}
        replaceMerge={['series']}
        lazyUpdate
        style={{ width: '100%', height: '100%' }}
      />
      <button
        type="button"
        className="epidemic-curve-reset"
        onClick={resetDateWindow}
        disabled={!canResetDateWindow}
        aria-label={lang === 'zh' ? '重置时间范围' : 'Reset time range'}
        title={lang === 'zh' ? '重置时间范围' : 'Reset time range'}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
          <path d="M3 3v5h5" />
        </svg>
      </button>
    </div>
  );
}
