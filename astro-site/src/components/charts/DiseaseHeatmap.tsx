// src/components/charts/DiseaseHeatmap.tsx
// OWID-style heatmap with ECharts rendering, adaptive width, and scrollable disease axis.

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import EChartsReact from '../../lib/echartsReact';
import echarts from '../../lib/echarts';
import ChartFrame from './ChartFrame';
import type { ChartSourceMeta } from '../../utils/chartMeta';
import {
  type CountryDatasetHeatmap,
  type CountryDatasetSeriesEntry,
} from './countryDataset';
import { useChartLanguage, useChartTheme } from './chartPreferences';
import { useCountryDataset } from './useCountryDataset';

type HeatmapData = CountryDatasetHeatmap;
type DiseaseSeriesEntry = CountryDatasetSeriesEntry;

type ZoomWindow = {
  startValue: number;
  endValue: number;
};

interface Props {
  data?: HeatmapData | null;
  series?: Record<string, DiseaseSeriesEntry>;
  dataUrl?: string;
  height?: number;
  sourceMeta?: ChartSourceMeta | null;
}

function fromLogValue(value: number) {
  return Math.round(Math.pow(10, value) - 1);
}

function normaliseMonthKey(value: string) {
  const monthMatch = value.match(/^(\d{4}-\d{2})/);
  if (monthMatch) return monthMatch[1];

  const parsed = new Date(value);
  if (!Number.isNaN(parsed.getTime())) return parsed.toISOString().slice(0, 7);

  return value.slice(0, 7);
}

function isSummaryRow(diseaseId?: string, label?: string) {
  const normalizedId = diseaseId?.toLowerCase();
  const normalizedLabel = label?.trim().toLowerCase();
  return normalizedId === 'd999' || normalizedLabel === 'total' || normalizedLabel === 'summary' || normalizedLabel === '合计';
}

export default function DiseaseHeatmap({ data = null, series: initialSeries, dataUrl, height = 600, sourceMeta = null }: Props) {
  const hasInitialData = Boolean(
    (data?.z?.length ?? 0) > 0
    || (initialSeries && Object.keys(initialSeries).length > 0)
  );
  const remoteDataset = useCountryDataset(dataUrl, !hasInitialData);
  const loadedData = (data?.z?.length ?? 0) > 0
    ? data
    : (remoteDataset.data?.heatmap ?? null);
  const series = initialSeries ?? remoteDataset.data?.disease_series;
  const theme = useChartTheme();
  const lang = useChartLanguage();
  const [zoomWindow, setZoomWindow] = useState<ZoomWindow>({ startValue: 0, endValue: 13 });

  const chartColors = useMemo(() => (
    theme === 'light'
      ? {
          font: '#556070',
          line: '#c9c2b8',
          hoverBg: '#fffdfa',
          hoverBorder: '#c9c2b8',
          hoverFont: '#162232',
          sliderBg: '#f7f3ec',
          fillerColor: 'rgba(33,95,124,0.16)',
          palette: ['#fbf5e8', '#eed795', '#dce7ee', '#6db5e6', '#0d5a86'],
        }
      : {
          font: '#a4b1c1',
          line: '#51667f',
          hoverBg: '#17283a',
          hoverBorder: '#51667f',
          hoverFont: '#f3f6fb',
          sliderBg: '#122132',
          fillerColor: 'rgba(118,183,178,0.16)',
          palette: ['#132130', '#344c5f', '#4c7d99', '#7cc3ee', '#f0c16a'],
        }
  ), [theme]);

  const activeData = useMemo<HeatmapData>(() => {
    if (series && Object.keys(series).length > 0) {
      const diseaseEntries = Object.values(series)
        .filter((entry) => entry && entry.category !== 'Summary')
        .sort((a, b) => {
          const totalGap = (b.total_cases ?? 0) - (a.total_cases ?? 0);
          if (totalGap !== 0) return totalGap;
          return (a.name_en || a.name_zh || a.disease_id).localeCompare(b.name_en || b.name_zh || b.disease_id);
        });

      const months = Array.from(new Set(
        diseaseEntries.flatMap((entry) => entry.dates.map(normaliseMonthKey).filter(Boolean))
      )).sort();

      return {
        diseases: diseaseEntries.map((entry) => entry.disease_id),
        disease_labels: diseaseEntries.map((entry) => (
          lang === 'zh'
            ? (entry.name_zh || entry.name_en || entry.disease_id)
            : (entry.name_en || entry.name_zh || entry.disease_id)
        )),
        months,
        z: diseaseEntries.map((entry) => {
          const monthTotals = new Map<string, number>();
          entry.dates.forEach((date, index) => {
            const monthKey = normaliseMonthKey(date);
            if (!monthKey) return;
            monthTotals.set(monthKey, (monthTotals.get(monthKey) ?? 0) + (entry.cases[index] ?? 0));
          });
          return months.map((month) => Math.log10((monthTotals.get(month) ?? 0) + 1));
        }),
      };
    }

    if (!loadedData || !loadedData.z || loadedData.z.length === 0) {
      return { diseases: [], disease_labels: [], months: [], z: [] };
    }

    const rowIndexes = loadedData.disease_labels
      .map((_, index) => index)
      .filter((index) => !isSummaryRow(loadedData.diseases?.[index], loadedData.disease_labels[index]));

    return {
      diseases: rowIndexes.map((index) => loadedData.diseases[index]),
      disease_labels: rowIndexes.map((index) => loadedData.disease_labels[index]),
      months: loadedData.months,
      z: rowIndexes.map((index) => loadedData.z[index]),
    };
  }, [loadedData, lang, series]);

  const hasData = activeData.z.length > 0 && activeData.months.length > 0;

  const heatmapData = useMemo(() => {
    const result: [number, number, number][] = [];
    activeData.z.forEach((row, rowIdx) => {
      row.forEach((logValue, colIdx) => {
        result.push([colIdx, rowIdx, logValue]);
      });
    });
    return result;
  }, [activeData.z]);

  const peakCell = useMemo<{ value: number; row: number; col: number } | null>(() => {
    let best: { value: number; row: number; col: number } | null = null;
    activeData.z.forEach((row, rowIdx) => {
      row.forEach((logValue, colIdx) => {
        if (!best || logValue > best.value) best = { value: logValue, row: rowIdx, col: colIdx };
      });
    });
    return best;
  }, [activeData.z]);

  const previewRows = useMemo(() => (
    activeData.disease_labels.map((label, rowIdx) => ({
      label,
      values: activeData.z[rowIdx].map(fromLogValue),
    }))
  ), [activeData.disease_labels, activeData.z]);

  const longestLabelLength = useMemo(
    () => activeData.disease_labels.reduce((max, label) => Math.max(max, label.length), 0),
    [activeData.disease_labels]
  );
  const maxLogValue = useMemo(
    () => Math.max(0, ...activeData.z.flat().filter((value) => Number.isFinite(value))),
    [activeData.z]
  );

  const chartHeight = Math.max(460, height);
  const monthCellWidth = activeData.months.length > 180 ? 10 : activeData.months.length > 120 ? 12 : activeData.months.length > 72 ? 14 : 18;
  const monthLabelInterval = Math.max(0, Math.ceil(activeData.months.length / 18) - 1);
  const visibleDiseaseCount = Math.min(
    activeData.disease_labels.length,
    activeData.disease_labels.length > 48 ? 18 : activeData.disease_labels.length > 28 ? 16 : 14
  );
  const needsVerticalZoom = activeData.disease_labels.length > visibleDiseaseCount;
  const verticalZoomEnd = Math.max(0, visibleDiseaseCount - 1);
  const maxDiseaseIndex = Math.max(0, activeData.disease_labels.length - 1);
  const fixedAxisWidth = Math.min(232, Math.max(124, longestLabelLength * 5.4 + (needsVerticalZoom ? 18 : 6)));
  const labelMaxChars = Math.max(16, Math.min(30, Math.floor((fixedAxisWidth - (needsVerticalZoom ? 38 : 14)) / 5.2)));
  const labelPixelWidth = Math.max(92, fixedAxisWidth - (needsVerticalZoom ? 34 : 12));
  const minHeatmapWidth = Math.max(720, activeData.months.length * monthCellWidth + 24);

  useEffect(() => {
    const initialEnd = needsVerticalZoom ? Math.min(verticalZoomEnd, maxDiseaseIndex) : maxDiseaseIndex;
    setZoomWindow({ startValue: 0, endValue: initialEnd });
  }, [maxDiseaseIndex, needsVerticalZoom, verticalZoomEnd]);

  const handleYAxisZoom = useCallback((_params: unknown, chart: any) => {
    if (!needsVerticalZoom) return;
    const option = chart?.getOption?.();
    const zooms = Array.isArray(option?.dataZoom) ? option.dataZoom : [];
    if (!zooms.length) return;

    const source = zooms.find((item: any) => item.type === 'slider') ?? zooms[0];
    const rawStart = Number.isFinite(source?.startValue) ? Number(source.startValue) : 0;
    const rawEnd = Number.isFinite(source?.endValue) ? Number(source.endValue) : verticalZoomEnd;
    const nextStart = Math.max(0, Math.min(rawStart, maxDiseaseIndex));
    const nextEnd = Math.max(nextStart, Math.min(rawEnd, maxDiseaseIndex));

    setZoomWindow((prev) => {
      if (prev.startValue === nextStart && prev.endValue === nextEnd) return prev;
      return { startValue: nextStart, endValue: nextEnd };
    });
  }, [maxDiseaseIndex, needsVerticalZoom, verticalZoomEnd]);

  const yAxisZoomControls = useMemo(() => {
    if (!needsVerticalZoom) return [];
    return [
      {
        type: 'inside' as const,
        yAxisIndex: 0,
        filterMode: 'weakFilter' as const,
        zoomOnMouseWheel: false,
        moveOnMouseWheel: true,
        moveOnMouseMove: true,
        startValue: zoomWindow.startValue,
        endValue: zoomWindow.endValue,
      },
      {
        type: 'slider' as const,
        yAxisIndex: 0,
        right: 2,
        top: 10,
        bottom: 52,
        width: 8,
        filterMode: 'weakFilter' as const,
        startValue: zoomWindow.startValue,
        endValue: zoomWindow.endValue,
        borderColor: chartColors.line,
        backgroundColor: chartColors.sliderBg,
        fillerColor: chartColors.fillerColor,
        showDetail: false,
        showDataShadow: false,
        brushSelect: false,
      },
    ];
  }, [
    chartColors.fillerColor,
    chartColors.line,
    chartColors.sliderBg,
    needsVerticalZoom,
    zoomWindow.endValue,
    zoomWindow.startValue,
  ]);

  const yAxisOption = useMemo(() => {
    return {
      animationDuration: 160,
      backgroundColor: 'transparent',
      grid: {
        left: 0,
        right: needsVerticalZoom ? 20 : 4,
        top: 10,
        bottom: 52,
        containLabel: true,
      },
      xAxis: {
        type: 'value' as const,
        min: 0,
        max: 1,
        show: false,
      },
      yAxis: {
        type: 'category' as const,
        inverse: true,
        data: activeData.disease_labels,
        axisLabel: {
          color: chartColors.font,
          fontSize: 10,
          margin: 3,
          width: labelPixelWidth,
          overflow: 'truncate' as const,
          formatter: (value: string) => value.length > labelMaxChars ? `${value.slice(0, labelMaxChars)}...` : value,
        },
        axisLine: { lineStyle: { color: chartColors.line } },
        axisTick: { show: false },
        splitLine: { show: false },
      },
      dataZoom: yAxisZoomControls,
      series: [
        {
          type: 'bar' as const,
          data: activeData.disease_labels.map(() => 1),
          silent: true,
          animation: false,
          barWidth: '70%',
          itemStyle: { color: 'transparent' },
        },
      ],
    };
  }, [
    activeData.disease_labels,
    chartColors.font,
    chartColors.line,
    labelMaxChars,
    labelPixelWidth,
    needsVerticalZoom,
    yAxisZoomControls,
  ]);

  const heatmapOption = useMemo(() => {
    return {
      animationDuration: 160,
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'item',
        backgroundColor: chartColors.hoverBg,
        borderColor: chartColors.hoverBorder,
        borderWidth: 1,
        textStyle: { color: chartColors.hoverFont, fontSize: 12 },
        formatter: (param: any) => {
          const row = param.value[1];
          const col = param.value[0];
          return `<b>${activeData.disease_labels[row]}</b><br/>${activeData.months[col]}<br/>${lang === 'zh' ? '病例数' : 'Cases'}: <b>${fromLogValue(param.value[2]).toLocaleString()}</b>`;
        },
      },
      grid: {
        left: 6,
        right: 8,
        top: 10,
        bottom: 52,
      },
      xAxis: {
        type: 'category' as const,
        data: activeData.months,
        axisLabel: {
          color: chartColors.font,
          fontSize: 10,
          rotate: -42,
          interval: monthLabelInterval,
        },
        axisLine: { lineStyle: { color: chartColors.line } },
        axisTick: { lineStyle: { color: chartColors.line } },
        splitLine: { show: false },
      },
      yAxis: {
        type: 'category' as const,
        inverse: true,
        data: activeData.disease_labels,
        axisLabel: { show: false },
        axisLine: { show: false },
        axisTick: { show: false },
        splitLine: { show: false },
      },
      dataZoom: needsVerticalZoom
        ? [{
            type: 'inside' as const,
            yAxisIndex: 0,
            filterMode: 'weakFilter' as const,
            zoomOnMouseWheel: false,
            moveOnMouseWheel: true,
            moveOnMouseMove: true,
            startValue: zoomWindow.startValue,
            endValue: zoomWindow.endValue,
          }]
        : [],
      series: [{
        type: 'heatmap' as const,
        data: heatmapData,
        progressive: 0,
        itemStyle: {
          borderColor: theme === 'light' ? 'rgba(255,255,255,0.28)' : 'rgba(255,255,255,0.05)',
          borderWidth: 0.4,
        },
        emphasis: {
          itemStyle: {
            borderColor: theme === 'light' ? '#0d5a86' : '#f0c16a',
            borderWidth: 1,
            shadowBlur: 6,
            shadowColor: theme === 'light' ? 'rgba(13,90,134,0.18)' : 'rgba(240,193,106,0.22)',
          },
        },
      }],
      visualMap: {
        show: false,
        min: 0,
        max: maxLogValue,
        calculable: false,
        inRange: { color: chartColors.palette },
      },
    };
  }, [
    activeData.disease_labels,
    activeData.months,
    chartColors.font,
    chartColors.hoverBg,
    chartColors.hoverBorder,
    chartColors.hoverFont,
    chartColors.line,
    chartColors.palette,
    heatmapData,
    lang,
    maxLogValue,
    monthLabelInterval,
    needsVerticalZoom,
    theme,
    zoomWindow.endValue,
    zoomWindow.startValue,
  ]);

  const yAxisEvents = useMemo(() => ({ datazoom: handleYAxisZoom }), [handleYAxisZoom]);

  const legendGradient = useMemo(
    () => `linear-gradient(90deg, ${chartColors.palette.join(', ')})`,
    [chartColors.palette]
  );

  const legendBreaks = useMemo(() => {
    const ratios = [0, 0.2, 0.4, 0.6, 0.8, 1];
    return ratios.map((ratio) => {
      const raw = fromLogValue(maxLogValue * ratio);
      const label = raw >= 10000
        ? `${Math.round(raw / 1000)}k`
        : raw.toLocaleString();
      return { ratio, label };
    });
  }, [maxLogValue]);

  const toolbar = (
    <>
      <span className="chart-chip">{lang === 'zh' ? `${activeData.disease_labels.length} 种疾病` : `${activeData.disease_labels.length} diseases`}</span>
      <span className="chart-chip">{lang === 'zh' ? `${activeData.months.length} 个时间点` : `${activeData.months.length} time points`}</span>
      {peakCell && (
        <span className="chart-chip">
          {lang === 'zh'
            ? `最高强度 ${activeData.disease_labels[peakCell.row]} / ${activeData.months[peakCell.col]}`
            : `Highest intensity ${activeData.disease_labels[peakCell.row]} / ${activeData.months[peakCell.col]}`}
        </span>
      )}
    </>
  );

  const legend = (
    <div className="pointer-events-none absolute bottom-1 left-1 z-[2]">
      <div className="rounded-none border border-[rgb(var(--border)/0.7)] bg-[rgb(var(--surface)/0.94)] px-2 py-1.5">
        <div className="relative w-[170px]">
          <div
            className="h-2 w-full rounded-none border"
            style={{
              background: legendGradient,
              borderColor: chartColors.line,
            }}
          />
          <div className="relative mt-1 h-4 w-full">
            {legendBreaks.map((item, index) => (
              <React.Fragment key={item.ratio}>
                <span
                  className="absolute top-0 h-2 w-px -translate-x-1/2 bg-[rgb(var(--text-faint))]"
                  style={{ left: `${item.ratio * 100}%` }}
                />
                <span
                  className="absolute top-2.5 whitespace-nowrap text-[9px] leading-none text-[rgb(var(--text-faint))]"
                  style={{
                    left: `${item.ratio * 100}%`,
                    transform: index === 0
                      ? 'translateX(0)'
                      : index === legendBreaks.length - 1
                        ? 'translateX(-100%)'
                        : 'translateX(-50%)',
                  }}
                >
                  {item.label}
                </span>
              </React.Fragment>
            ))}
          </div>
        </div>
      </div>
    </div>
  );

  const renderTable = () => (
    <>
      <div className="data-preview-meta">
        {lang === 'zh'
          ? `原始数据预览，当前表格已包含全部 ${previewRows.length} 种疾病的月度病例数。`
          : `Raw data preview covering all ${previewRows.length} diseases with monthly case totals.`}
      </div>
      <table className="data-preview-table">
        <thead>
          <tr>
            <th className="is-sticky">{lang === 'zh' ? '疾病' : 'Disease'}</th>
            {activeData.months.map((month) => (
              <th key={month}>{month}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {previewRows.map((row) => (
            <tr key={row.label}>
              <td className="is-sticky">{row.label}</td>
              {row.values.map((value, index) => (
                <td key={`${row.label}-${activeData.months[index]}`}>{value.toLocaleString()}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );

  if (remoteDataset.loadError && !hasData) {
    return (
      <div className="chart-shell flex min-h-[160px] flex-col items-center justify-center gap-3 text-slate-500 text-sm" role="alert">
        <span>{lang === 'zh' ? '热图数据加载失败或请求超时。' : 'Heatmap data failed to load or the request timed out.'}</span>
        <button type="button" className="chart-link-btn" onClick={remoteDataset.retry}>
          {lang === 'zh' ? '重新加载' : 'Try again'}
        </button>
      </div>
    );
  }

  if (!hasData) {
    if (remoteDataset.isLoading) {
      return (
        <div className="chart-loading-shell" role="status" aria-busy="true" aria-label={lang === 'zh' ? '热图数据加载中' : 'Loading heatmap data'}>
          <div className="chart-loading-toolbar" aria-hidden="true">
            <span className="chart-loading-pill w-28" />
            <span className="chart-loading-pill w-36" />
            <span className="chart-loading-pill w-24" />
          </div>
          <div className="chart-loading-line w-3/4" aria-hidden="true" />
          <div className="chart-loading-panel" style={{ height }} aria-hidden="true" />
        </div>
      );
    }
    return (
      <div className="chart-shell flex items-center justify-center text-slate-500 text-sm min-h-[160px]">
        {lang === 'zh' ? '暂无数据' : 'No data available'}
      </div>
    );
  }

  return (
    <ChartFrame
      lang={lang}
      toolbar={toolbar}
      chart={({ isFullscreen }) => (
        <div
          className="grid min-h-0 w-full items-stretch gap-2"
          style={{
            gridTemplateColumns: `${fixedAxisWidth}px minmax(0, 1fr)`,
          }}
        >
          <div className="relative min-h-0" style={{ borderRight: '1px solid rgb(var(--border) / 0.65)', paddingRight: '0.28rem' }}>
            <EChartsReact
              echarts={echarts}
              option={yAxisOption}
              onEvents={yAxisEvents}
              notMerge
              style={{
                width: '100%',
                height: isFullscreen ? '100%' : chartHeight,
              }}
            />
            {legend}
          </div>
          <div className="chart-scroll-x">
            <EChartsReact
              echarts={echarts}
              option={heatmapOption}
              notMerge
              style={{
                width: '100%',
                minWidth: `${minHeatmapWidth}px`,
                height: isFullscreen ? '100%' : chartHeight,
              }}
            />
          </div>
        </div>
      )}
      table={renderTable}
      sourceMeta={sourceMeta}
    />
  );
}
