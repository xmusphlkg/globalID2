import { useCallback, useMemo } from 'react';
import EChartsReact from 'echarts-for-react/lib/core';
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
  const activePointCount = useMemo(
    () => lines.reduce((total, line) => total + line.values.length, 0),
    [lines]
  );

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
    if (nextWindow) onDateWindowChange(nextWindow);
  }, [activeDates, onDateWindowChange]);

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
    grid: { left: 64, right: 18, top: title ? 38 : 16, bottom: 54 },
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
        throttle: 50,
      },
      {
        id: 'epidemic-zoom-slider',
        type: 'slider' as const,
        xAxisIndex: 0,
        filterMode: 'filter' as const,
        startValue: dateWindow?.startDate,
        endValue: dateWindow?.endDate,
        throttle: 50,
        realtime: true,
        bottom: 8,
        height: 16,
        backgroundColor: colors.sliderBg,
        borderColor: colors.line,
        textStyle: { color: colors.font, fontSize: 10 },
        fillerColor: colors.fillerColor,
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
    <EChartsReact
      echarts={echarts}
      option={option}
      onEvents={chartEvents}
      replaceMerge={['series']}
      lazyUpdate
      style={{ width: '100%', height }}
    />
  );
}
