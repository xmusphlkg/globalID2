// src/components/charts/DiseaseHeatmap.tsx
// OWID-style heatmap with data preview table and source footer.

import React, { useEffect, useMemo, useState } from 'react';
import EChartsReact from 'echarts-for-react/lib/core';
import echarts from '../../lib/echarts';
import ChartFrame from './ChartFrame';
import type { ChartSourceMeta } from '../../utils/chartMeta';

interface HeatmapData {
  diseases: string[];
  disease_labels: string[];
  months: string[];
  z: number[][];
}

interface DiseaseSeriesEntry {
  disease_id: string;
  name_en: string;
  name_zh: string;
  category?: string;
  dates: string[];
  cases: number[];
  total_cases: number;
}

interface Props {
  data?: HeatmapData | null;
  series?: Record<string, DiseaseSeriesEntry>;
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

export default function DiseaseHeatmap({ data = null, series, height = 600, sourceMeta = null }: Props) {
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

  const chartColors = useMemo(() => (
    theme === 'light'
      ? {
          font: '#556070',
          line: '#c9c2b8',
          hoverBg: '#fffdfa',
          hoverBorder: '#c9c2b8',
          hoverFont: '#162232',
          palette: ['#fff7eb', '#f2e5b7', '#c7dff0', '#56b4e9', '#d55e00'],
        }
      : {
          font: '#a4b1c1',
          line: '#51667f',
          hoverBg: '#17283a',
          hoverBorder: '#51667f',
          hoverFont: '#f3f6fb',
          palette: ['#112132', '#29465d', '#2f6f91', '#56b4e9', '#f0b35f'],
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

    if (!data || !data.z || data.z.length === 0) {
      return { diseases: [], disease_labels: [], months: [], z: [] };
    }

    const rowIndexes = data.disease_labels
      .map((_, index) => index)
      .filter((index) => !isSummaryRow(data.diseases?.[index], data.disease_labels[index]));

    return {
      diseases: rowIndexes.map((index) => data.diseases[index]),
      disease_labels: rowIndexes.map((index) => data.disease_labels[index]),
      months: data.months,
      z: rowIndexes.map((index) => data.z[index]),
    };
  }, [data, lang, series]);
  const hasData = activeData.z.length > 0;

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

  const previewRowCount = activeData.disease_labels.length;
  const previewRows = useMemo(() => (
    activeData.disease_labels.map((label, rowIdx) => ({
      label,
      values: activeData.z[rowIdx].map(fromLogValue),
    }))
  ), [activeData.disease_labels, activeData.z, previewRowCount]);

  const longestLabelLength = useMemo(
    () => activeData.disease_labels.reduce((max, label) => Math.max(max, label.length), 0),
    [activeData.disease_labels]
  );
  const dynamicHeight = Math.max(height, Math.min(activeData.disease_labels.length * 18 + 220, 920));
  const leftLabelSpace = Math.min(340, Math.max(190, longestLabelLength * 7 + 44));
  const chartWidth = Math.max(960, activeData.months.length * 22 + leftLabelSpace + 160);
  const monthLabelInterval = Math.max(0, Math.ceil(activeData.months.length / 18) - 1);
  const maxLogValue = Math.max(0, ...activeData.z.flat().filter((value) => Number.isFinite(value)));

  const option = useMemo(() => ({
    animationDuration: 240,
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      backgroundColor: chartColors.hoverBg,
      borderColor: chartColors.hoverBorder,
      textStyle: { color: chartColors.hoverFont, fontSize: 12 },
      formatter: (param: any) => {
        const row = param.value[1];
        const col = param.value[0];
        return `<b>${activeData.disease_labels[row]}</b><br/>${activeData.months[col]}<br/>${lang === 'zh' ? '病例数' : 'Cases'}: <b>${fromLogValue(param.value[2]).toLocaleString()}</b>`;
      },
    },
    grid: { left: leftLabelSpace, right: 96, top: 16, bottom: 82 },
    xAxis: {
      type: 'category' as const,
      data: activeData.months,
      axisLabel: {
        color: chartColors.font,
        fontSize: 10,
        rotate: -40,
        interval: monthLabelInterval,
      },
      axisLine: { lineStyle: { color: chartColors.line } },
      axisTick: { lineStyle: { color: chartColors.line } },
    },
    yAxis: {
      type: 'category' as const,
      data: activeData.disease_labels,
      axisLabel: {
        color: chartColors.font,
        fontSize: 11,
        formatter: (value: string) => value.length > 40 ? `${value.slice(0, 40)}...` : value,
      },
      axisLine: { lineStyle: { color: chartColors.line } },
      axisTick: { lineStyle: { color: chartColors.line } },
    },
    visualMap: {
      min: 0,
      max: maxLogValue,
      orient: 'vertical' as const,
      calculable: false,
      right: 8,
      top: 20,
      bottom: 86,
      text: [lang === 'zh' ? '高' : 'High', lang === 'zh' ? '低' : 'Low'],
      textStyle: { color: chartColors.font, fontSize: 10 },
      inRange: { color: chartColors.palette },
    },
    dataZoom: activeData.disease_labels.length > 20
      ? [{
          type: 'slider' as const,
          yAxisIndex: 0,
          right: 72,
          width: 14,
          filterMode: 'empty' as const,
          start: 0,
          end: Math.max(8, Math.min(100, (20 / activeData.disease_labels.length) * 100)),
        }]
      : [],
    series: [{
      type: 'heatmap' as const,
      data: heatmapData,
      itemStyle: { borderColor: 'rgba(255,255,255,0.06)', borderWidth: 0.5 },
      emphasis: { itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0,0,0,0.25)' } },
    }],
  }), [activeData.disease_labels, activeData.months, activeData.z, chartColors, heatmapData, lang, leftLabelSpace, maxLogValue, monthLabelInterval]);

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
    ? '热图现已覆盖全部非汇总疾病。颜色使用色盲友好的蓝橙双色渐变，强度仍按对数刻度计算；月份较多时支持横向滚动查看。'
    : 'The heatmap now includes every non-summary disease. Colors use a colorblind-friendly blue-orange ramp while intensity remains logarithmic; long timelines can be explored with horizontal scrolling.';

  const table = (
    <>
      <div className="data-preview-meta">
        {lang === 'zh'
          ? `原始数据预览，当前表格已包含全部 ${previewRowCount} 种疾病的月度病例数。`
          : `Raw data preview covering all ${previewRowCount} diseases with monthly case totals.`}
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

  if (!hasData) {
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
      note={note}
      chart={({ isFullscreen }) => (
        <div className="chart-scroll-x">
          <EChartsReact
            echarts={echarts}
            option={option}
            notMerge
            style={{
              width: chartWidth,
              minWidth: '100%',
              height: isFullscreen ? 'clamp(560px, calc(100vh - 250px), 960px)' : dynamicHeight,
            }}
          />
        </div>
      )}
      table={table}
      sourceMeta={sourceMeta}
    />
  );
}
