import { useCallback, useEffect, useMemo, useRef } from 'react';
import EChartsReact from '../../lib/echartsReact';
import echarts from '../../lib/echarts';
import {
  ANIMATION_POINT_LIMIT,
  METRIC_LABELS,
  dateWindowFromZoom,
  formatTemporalGranularity,
  insertMissingPeriodBreaks,
  normalizeTemporalGranularity,
  type DateWindow,
  type EpidemicAnalysisMode,
  type EpidemicMetric,
} from './epidemicCurveModel';

export interface CurveEvent {
  date: string;
  label: string;
}

export interface CurveHistoricalReference {
  expected: (number | null)[];
  lower: (number | null)[];
  upper: (number | null)[];
}

export interface EpidemicCurveLine {
  id: string;
  name: string;
  color: string;
  dates: string[];
  values: (number | null)[];
  granularity: string;
  reportingBasis?: string;
  timeBasis?: string;
  sourceLabel?: string;
  provisionalFrom?: string | null;
  events?: CurveEvent[];
  reference?: CurveHistoricalReference;
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
  referenceBand: string;
  provisionalArea: string;
  title: string;
}

interface Props {
  lines: EpidemicCurveLine[];
  activeDates: string[];
  dateWindow: DateWindow | null;
  onDateWindowChange: (dateWindow: DateWindow) => void;
  metric: EpidemicMetric;
  metricLabel?: string;
  lang: 'en' | 'zh';
  colors: ChartColors;
  title?: string;
  height: number | string;
  facetByGranularity?: boolean;
  analysisMode?: EpidemicAnalysisMode;
  emptyMessage?: string;
}

function formatTooltipValue(value: unknown, metric: EpidemicMetric) {
  const numericValue = Number(value);
  if (!Number.isFinite(numericValue)) return '—';
  return ['incidence_rates', 'trend_index'].includes(metric)
    ? numericValue.toFixed(2)
    : numericValue.toLocaleString();
}

const GRANULARITY_ORDER = ['annual', 'quarterly', 'monthly', 'weekly', 'daily', 'unknown'];

export default function EpidemicCurvePlot({
  lines,
  activeDates,
  dateWindow,
  onDateWindowChange,
  metric,
  metricLabel,
  lang,
  colors,
  title,
  height,
  facetByGranularity = false,
  analysisMode = 'monitor',
  emptyMessage,
}: Props) {
  const zoomCommitTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const plotRef = useRef<HTMLDivElement>(null);
  const chartInstanceRef = useRef<any>(null);
  const resizeFramesRef = useRef<[number, number]>([0, 0]);
  const scheduleChartResize = useCallback(() => {
    const [firstFrame, secondFrame] = resizeFramesRef.current;
    cancelAnimationFrame(firstFrame);
    cancelAnimationFrame(secondFrame);
    resizeFramesRef.current[0] = requestAnimationFrame(() => {
      resizeFramesRef.current[1] = requestAnimationFrame(() => {
        const plot = plotRef.current;
        const instance = chartInstanceRef.current;
        if (!plot || plot.clientWidth <= 0 || plot.clientHeight <= 0) return;
        if (instance && !instance.isDisposed?.()) {
          instance.resize({ width: plot.clientWidth, height: plot.clientHeight });
        }
      });
    });
  }, []);
  const handleChartReady = useCallback((instance: any) => {
    chartInstanceRef.current = instance;
    scheduleChartResize();
  }, [scheduleChartResize]);
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
  const lineGroups = useMemo(() => {
    if (!facetByGranularity) return [{ granularity: 'all', lines }];
    const grouped = new Map<string, EpidemicCurveLine[]>();
    lines.forEach((line) => {
      const granularity = normalizeTemporalGranularity(line.granularity);
      grouped.set(granularity, [...(grouped.get(granularity) ?? []), line]);
    });
    return [...grouped.entries()]
      .sort(([left], [right]) => {
        const leftIndex = GRANULARITY_ORDER.indexOf(left);
        const rightIndex = GRANULARITY_ORDER.indexOf(right);
        return (leftIndex < 0 ? GRANULARITY_ORDER.length : leftIndex)
          - (rightIndex < 0 ? GRANULARITY_ORDER.length : rightIndex);
      })
      .map(([granularity, groupLines]) => ({ granularity, lines: groupLines }));
  }, [facetByGranularity, lines]);

  useEffect(() => () => {
    if (zoomCommitTimer.current) clearTimeout(zoomCommitTimer.current);
  }, []);

  useEffect(() => {
    const plot = plotRef.current;
    if (!plot) return undefined;

    let disposed = false;

    // Hydration, the wide selector breakpoint, web-font completion and the
    // Fullscreen API can all settle on different frames. Observing the real
    // plot box prevents ECharts from retaining the large pre-layout canvas it
    // may receive during its first mount.
    const observer = new ResizeObserver(scheduleChartResize);
    observer.observe(plot);
    window.addEventListener('resize', scheduleChartResize);
    document.addEventListener('fullscreenchange', scheduleChartResize);
    void document.fonts?.ready.then(() => {
      if (!disposed) scheduleChartResize();
    });
    scheduleChartResize();

    return () => {
      disposed = true;
      observer.disconnect();
      window.removeEventListener('resize', scheduleChartResize);
      document.removeEventListener('fullscreenchange', scheduleChartResize);
      cancelAnimationFrame(resizeFramesRef.current[0]);
      cancelAnimationFrame(resizeFramesRef.current[1]);
      chartInstanceRef.current = null;
    };
  }, [scheduleChartResize]);

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

  const option = useMemo(() => {
    const multiPanel = lineGroups.length > 1;
    const xAxisIndexes = lineGroups.map((_, index) => index);
    const panelTopStart = title ? 12 : 4;
    const panelBottomReserve = 16;
    const panelSlot = (100 - panelTopStart - panelBottomReserve) / lineGroups.length;
    const metricName = metricLabel ?? METRIC_LABELS[metric][lang];
    const panelTitles = multiPanel
      ? lineGroups.map((group, index) => ({
          text: formatTemporalGranularity(group.granularity, lang),
          textStyle: { color: colors.title, fontSize: 12, fontWeight: 600 },
          left: 64,
          top: `${panelTopStart + (index * panelSlot)}%`,
        }))
      : [];
    const mainTitle = title
      ? [{
          text: title,
          textStyle: { color: colors.title, fontSize: 15, fontWeight: 600 },
          left: 0,
          top: 0,
        }]
      : [];
    const grids = multiPanel
      ? lineGroups.map((_, index) => ({
          left: 64,
          right: 18,
          top: `${panelTopStart + (index * panelSlot) + 4}%`,
          height: `${Math.max(10, panelSlot - 7)}%`,
        }))
      : [{ left: 64, right: 18, top: title ? 48 : 32, bottom: 70 }];
    const xAxes = lineGroups.map((_, index) => ({
      type: 'time' as const,
      gridIndex: index,
      axisLabel: {
        color: colors.font,
        fontSize: 11,
        show: !multiPanel || index === lineGroups.length - 1,
        hideOverlap: true,
      },
      axisLine: { lineStyle: { color: colors.line } },
      axisTick: { lineStyle: { color: colors.line }, show: !multiPanel || index === lineGroups.length - 1 },
      splitLine: { lineStyle: { color: colors.grid, type: 'solid' as const } },
    }));
    const yAxes = lineGroups.map((_, index) => ({
      type: 'value' as const,
      gridIndex: index,
      name: multiPanel ? undefined : metricName,
      nameTextStyle: { color: colors.font, fontSize: 11, padding: [0, 0, 8, 0] },
      axisLabel: { color: colors.font, fontSize: 11 },
      axisLine: { lineStyle: { color: colors.line } },
      axisTick: { lineStyle: { color: colors.line } },
      splitLine: { lineStyle: { color: colors.grid, type: 'solid' as const } },
      min: 0,
      max: metric === 'trend_index' ? 100 : undefined,
    }));

    return {
      animation: activePointCount <= ANIMATION_POINT_LIMIT,
      animationDuration: 180,
      backgroundColor: 'transparent',
      title: [...mainTitle, ...panelTitles],
      tooltip: {
        trigger: 'axis',
        axisPointer: {
          type: 'line' as const,
          lineStyle: { color: colors.line, type: 'solid' as const },
        },
        backgroundColor: colors.hoverBg,
        borderColor: colors.hoverBorder,
        borderWidth: 1,
        textStyle: { color: colors.hoverFont, fontSize: 12 },
          formatter: (params: any[]) => {
            const date = params[0]?.axisValueLabel ?? params[0]?.axisValue ?? '';
            const visibleParams = params.filter((param) => (
              !String(param.seriesName ?? '').startsWith('__reference_')
            ));
            return [
              `<b>${date}</b>`,
            ...visibleParams.map((param) => (
              `${param.marker}${param.seriesName}: <b>${formatTooltipValue(param.value?.[1] ?? param.value, metric)}</b>`
            )),
          ].join('<br/>');
        },
      },
      axisPointer: multiPanel ? { link: [{ xAxisIndex: 'all' }] } : undefined,
      grid: grids,
      xAxis: xAxes,
      yAxis: yAxes,
      dataZoom: [
        {
          id: 'epidemic-zoom-inside',
          type: 'inside' as const,
          xAxisIndex: xAxisIndexes,
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
          xAxisIndex: xAxisIndexes,
          filterMode: 'filter' as const,
          startValue: dateWindow?.startDate,
          endValue: dateWindow?.endDate,
          throttle: 16,
          realtime: true,
          left: 64,
          right: 58,
          bottom: 16,
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
          moveHandleStyle: { color: colors.hoverBorder, opacity: 0.72 },
          emphasis: {
            handleStyle: {
              color: colors.hoverBg,
              borderColor: colors.hoverFont,
              borderWidth: 2,
            },
            moveHandleStyle: { color: colors.hoverFont, opacity: 0.9 },
          },
        },
      ],
      series: lineGroups.flatMap((group, groupIndex) => (
        group.lines.flatMap((line, lineIndex) => {
          const withBreaks = insertMissingPeriodBreaks(
            line.dates,
            line.values,
            line.granularity
          );
          const eventLines = (line.events ?? []).map((event) => ({
            xAxis: event.date,
            name: event.label,
            label: { formatter: event.label },
          }));
          const visibleProvisionalFrom = line.provisionalFrom
            ? line.dates.find((date) => date >= line.provisionalFrom!) ?? null
            : null;
          if (metric === 'historical_index' && lineIndex === 0) {
            eventLines.push({
              yAxis: 100,
              name: lang === 'zh' ? '历史预期' : 'Historical expected',
              label: { formatter: lang === 'zh' ? '历史预期 100' : 'Expected 100' },
            } as any);
          }
          const mainSeries = {
            id: line.id,
            name: line.name,
            type: analysisMode === 'outbreak' ? 'bar' as const : 'line' as const,
            xAxisIndex: groupIndex,
            yAxisIndex: groupIndex,
            showSymbol: false,
            symbol: 'circle',
            symbolSize: 7,
            smooth: false,
            connectNulls: false,
            sampling: analysisMode === 'outbreak' ? undefined : 'lttb' as const,
            barMaxWidth: analysisMode === 'outbreak' ? 26 : undefined,
            barCategoryGap: analysisMode === 'outbreak' ? '0%' : undefined,
            lineStyle: analysisMode === 'outbreak' ? undefined : {
              color: line.color,
              width: 2.4,
              type: 'solid' as const,
            },
            itemStyle: { color: line.color },
            emphasis: { focus: 'series' as const, scale: true },
            z: 10 + lineIndex,
            markArea: visibleProvisionalFrom ? {
              silent: true,
              itemStyle: { color: colors.provisionalArea },
              label: {
                show: true,
                color: colors.font,
                fontSize: 10,
                formatter: lang === 'zh' ? '暂定／可能不完整' : 'Provisional / may be incomplete',
                position: 'insideTopRight',
              },
              data: [[
                { xAxis: visibleProvisionalFrom },
                { xAxis: line.dates[line.dates.length - 1] },
              ]],
            } : undefined,
            markLine: eventLines.length > 0 ? {
              silent: true,
              symbol: ['none', 'none'],
              lineStyle: { color: colors.line, type: 'solid' as const, width: 1.2 },
              label: { color: colors.font, fontSize: 10, rotate: 90, position: 'insideEndTop' },
              data: eventLines,
            } : undefined,
            data: withBreaks.dates.map((date, valueIndex) => [
              date,
              withBreaks.values[valueIndex] ?? null,
            ]),
          };
          if (!line.reference || analysisMode !== 'monitor') return [mainSeries];

          const lower = insertMissingPeriodBreaks(
            line.dates,
            line.reference.lower,
            line.granularity
          );
          const upperRangeValues = line.reference.upper.map((value, index) => {
            const low = line.reference?.lower[index];
            return value == null || low == null ? null : Math.max(0, value - low);
          });
          const upperRange = insertMissingPeriodBreaks(
            line.dates,
            upperRangeValues,
            line.granularity
          );
          const expected = insertMissingPeriodBreaks(
            line.dates,
            line.reference.expected,
            line.granularity
          );
          const stackName = `reference-${line.id}`;
          return [
            {
              id: `__reference_base_${line.id}`,
              name: `__reference_base_${line.name}`,
              type: 'line' as const,
              xAxisIndex: groupIndex,
              yAxisIndex: groupIndex,
              stack: stackName,
              silent: true,
              symbol: 'none',
              lineStyle: { opacity: 0 },
              areaStyle: { opacity: 0 },
              data: lower.dates.map((date, index) => [date, lower.values[index]]),
              z: 1,
            },
            {
              id: `__reference_range_${line.id}`,
              name: `__reference_range_${line.name}`,
              type: 'line' as const,
              xAxisIndex: groupIndex,
              yAxisIndex: groupIndex,
              stack: stackName,
              silent: true,
              symbol: 'none',
              lineStyle: { opacity: 0 },
              areaStyle: { color: colors.referenceBand, opacity: 1 },
              data: upperRange.dates.map((date, index) => [date, upperRange.values[index]]),
              z: 1,
            },
            {
              id: `__reference_expected_${line.id}`,
              name: lang === 'zh' ? '历史中位预期' : 'Historical median expected',
              type: 'line' as const,
              xAxisIndex: groupIndex,
              yAxisIndex: groupIndex,
              silent: true,
              showSymbol: false,
              connectNulls: false,
              lineStyle: { color: colors.font, type: 'solid' as const, width: 1.4, opacity: 0.68 },
              data: expected.dates.map((date, index) => [date, expected.values[index]]),
              z: 4,
            },
            mainSeries,
          ];
        })
      )),
    };
  }, [activePointCount, analysisMode, colors, dateWindow, lang, lineGroups, metric, metricLabel, title]);

  return (
    <div ref={plotRef} className="epidemic-curve-plot" style={{ height }}>
      <EChartsReact
        echarts={echarts}
        option={option}
        onEvents={chartEvents}
        onChartReady={handleChartReady}
        replaceMerge={['series', 'grid', 'xAxis', 'yAxis', 'title', 'dataZoom']}
        lazyUpdate
        style={{ width: '100%', height: '100%' }}
      />
      {lines.length === 0 && (
        <div className="epidemic-curve-empty" role="status">
          {emptyMessage ?? (lang === 'zh' ? '当前选择没有可绘制数据' : 'No plottable data for the current selection')}
        </div>
      )}
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
