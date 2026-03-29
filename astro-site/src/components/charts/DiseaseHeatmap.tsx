// src/components/charts/DiseaseHeatmap.tsx
// OWID-style heatmap with ECharts rendering, adaptive width, and scrollable disease axis.

import React, { useEffect, useMemo, useState } from 'react';
import EChartsReact from 'echarts-for-react/lib/core';
import echarts from '../../lib/echarts';
import ChartFrame from './ChartFrame';
import type { ChartSourceMeta } from '../../utils/chartMeta';
import {
  loadCountryDataset,
  type CountryDatasetHeatmap,
  type CountryDatasetSeriesEntry,
} from './countryDataset';

type HeatmapData = CountryDatasetHeatmap;
type DiseaseSeriesEntry = CountryDatasetSeriesEntry;

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
  const [loadedData, setLoadedData] = useState<HeatmapData | null>(data);
  const [series, setSeries] = useState<Record<string, DiseaseSeriesEntry> | undefined>(initialSeries);
  const [loadError, setLoadError] = useState(false);
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    if (typeof document === 'undefined') return 'light';
    return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  });
  const [lang] = useState<'en' | 'zh'>(() => {
    if (typeof window !== 'undefined') return (localStorage.getItem('lang') as 'en' | 'zh') || 'en';
    return 'en';
  });

  useEffect(() => {
    if (typeof document === 'undefined') return;
    const root = document.documentElement;
    const updateTheme = () => setTheme(root.getAttribute('data-theme') === 'light' ? 'light' : 'dark');
    updateTheme();
    const observer = new MutationObserver(updateTheme);
    observer.observe(root, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (data && ((data.z?.length ?? 0) > 0 || initialSeries)) {
      setLoadedData(data);
      setSeries(initialSeries);
      setLoadError(false);
      return;
    }
    if (!dataUrl) return;

    let cancelled = false;
    loadCountryDataset(dataUrl)
      .then((dataset) => {
        if (cancelled) return;
        setLoadedData(dataset.heatmap ?? null);
        setSeries(dataset.disease_series);
        setLoadError(false);
      })
      .catch(() => {
        if (cancelled) return;
        setLoadError(true);
      });

    return () => {
      cancelled = true;
    };
  }, [data, dataUrl, initialSeries]);

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

  const peakCell = useMemo(() => {
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
  const leftLabelSpace = Math.min(280, Math.max(180, longestLabelLength * 6.6 + 28));
  const monthCellWidth = activeData.months.length > 180 ? 10 : activeData.months.length > 120 ? 12 : activeData.months.length > 72 ? 14 : 18;
  const minChartWidth = Math.max(960, leftLabelSpace + activeData.months.length * monthCellWidth + 40);
  const monthLabelInterval = Math.max(0, Math.ceil(activeData.months.length / 18) - 1);
  const visibleDiseaseCount = Math.min(
    activeData.disease_labels.length,
    activeData.disease_labels.length > 48 ? 18 : activeData.disease_labels.length > 28 ? 16 : 14
  );

  const option = useMemo(() => {
    const zoomControls: Array<Record<string, unknown>> = [];

    if (activeData.disease_labels.length > visibleDiseaseCount) {
      zoomControls.push(
        {
          type: 'inside' as const,
          yAxisIndex: 0,
          filterMode: 'weakFilter' as const,
          zoomOnMouseWheel: false,
          moveOnMouseWheel: true,
          moveOnMouseMove: true,
        },
        {
          type: 'slider' as const,
          yAxisIndex: 0,
          right: 6,
          top: 16,
          bottom: 58,
          width: 10,
          filterMode: 'weakFilter' as const,
          startValue: 0,
          endValue: Math.max(0, visibleDiseaseCount - 1),
          borderColor: chartColors.line,
          backgroundColor: chartColors.sliderBg,
          fillerColor: chartColors.fillerColor,
          showDetail: false,
          showDataShadow: false,
          brushSelect: false,
        },
      );
    }

    return {
      animationDuration: 240,
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
        left: leftLabelSpace,
        right: activeData.disease_labels.length > visibleDiseaseCount ? 26 : 14,
        top: 16,
        bottom: 58,
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
        axisLabel: {
          color: chartColors.font,
          fontSize: 11,
          margin: 10,
          formatter: (value: string) => value.length > 34 ? `${value.slice(0, 34)}...` : value,
        },
        axisLine: { lineStyle: { color: chartColors.line } },
        axisTick: { show: false },
        splitLine: { show: false },
      },
      dataZoom: zoomControls,
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
    chartColors.fillerColor,
    chartColors.font,
    chartColors.hoverBg,
    chartColors.hoverBorder,
    chartColors.hoverFont,
    chartColors.line,
    chartColors.palette,
    chartColors.sliderBg,
    heatmapData,
    lang,
    leftLabelSpace,
    maxLogValue,
    monthLabelInterval,
    theme,
    visibleDiseaseCount,
  ]);

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

  const note = lang === 'zh'
    ? '热图已切回 ECharts：横向默认铺满容器，月份过长时才出现横向滚动；颜色图例隐藏，疾病列表通过右侧纵向滚动条浏览。'
    : 'The heatmap is back on ECharts: it fills the available width first and only adds horizontal scrolling for long timelines. The color legend is hidden, and diseases are browsed through the vertical range control on the right.';

  const table = (
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

  if (loadError) {
    return (
      <div className="chart-shell flex items-center justify-center text-slate-500 text-sm min-h-[160px]">
        {lang === 'zh' ? '热图数据加载失败' : 'Failed to load heatmap data'}
      </div>
    );
  }

  if (!hasData) {
    return (
      <div className="chart-shell flex items-center justify-center text-slate-500 text-sm min-h-[160px]">
        {dataUrl ? (lang === 'zh' ? '热图数据加载中' : 'Loading heatmap data') : (lang === 'zh' ? '暂无数据' : 'No data available')}
      </div>
    );
  }

  return (
    <ChartFrame
      lang={lang}
      toolbar={toolbar}
      note={note}
      chart={({ isFullscreen }) => (
        <div className="chart-scroll-x">
          <EChartsReact
            echarts={echarts}
            option={option}
            notMerge
            style={{
              width: '100%',
              minWidth: `${minChartWidth}px`,
              height: isFullscreen ? '100%' : chartHeight,
            }}
          />
        </div>
      )}
      table={table}
      sourceMeta={sourceMeta}
    />
  );
}
