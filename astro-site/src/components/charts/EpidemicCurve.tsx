// src/components/charts/EpidemicCurve.tsx
// ECharts time-series chart for disease cases/deaths/incidence rates.
// Accepts pre-structured trace data from the Python export.

import React, { useState, useCallback, useEffect, useMemo } from 'react';
import EChartsReact from 'echarts-for-react/lib/core';
import echarts from '../../lib/echarts';

interface DiseaseSeries {
  disease_id: string;
  name_en: string;
  name_zh: string;
  category?: string;
  dates: string[];
  cases: number[];
  deaths: number[];
  incidence_rates: (number | null)[];
  total_cases: number;
}

interface Props {
  series: Record<string, DiseaseSeries>;
  title?: string;
  /** Limit to top N diseases by total cases */
  topN?: number;
  /** Allow restricting to specific disease IDs */
  diseasIds?: string[];
  height?: number;
}

type Metric = 'cases' | 'deaths' | 'incidence_rates';

const METRIC_LABELS: Record<Metric, { en: string; zh: string }> = {
  cases: { en: 'Cases', zh: '病例数' },
  deaths: { en: 'Deaths', zh: '死亡数' },
  incidence_rates: { en: 'Incidence Rate (per 100k)', zh: '发病率（每10万）' },
};

const PALETTE = [
  '#60a5fa', '#34d399', '#f472b6', '#fb923c', '#a78bfa',
  '#38bdf8', '#4ade80', '#fbbf24', '#e879f9', '#f87171',
  '#22d3ee', '#86efac', '#fca5a1', '#c084fc', '#fdba74',
];

const LIGHT_PALETTE = [
  '#2563eb', '#0d9488', '#db2777', '#ea580c', '#7c3aed',
  '#0284c7', '#16a34a', '#ca8a04', '#be185d', '#dc2626',
  '#0891b2', '#65a30d', '#9f1239', '#6d28d9', '#c2410c',
];

export default function EpidemicCurve({ series, title, topN = 10, diseasIds, height = 420 }: Props) {
  const [metric, setMetric] = useState<Metric>('cases');
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    if (typeof document === 'undefined') return 'dark';
    return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  });
  const [lang] = useState<'en' | 'zh'>(() => {
    if (typeof window !== 'undefined') return (localStorage.getItem('lang') as 'en' | 'zh') || 'en';
    return 'en';
  });

  useEffect(() => {
    if (typeof document === 'undefined') return;
    const root = document.documentElement;
    const updateTheme = () => {
      setTheme(root.getAttribute('data-theme') === 'light' ? 'light' : 'dark');
    };
    updateTheme();
    const observer = new MutationObserver(updateTheme);
    observer.observe(root, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);

  const chartColors = useMemo(() => {
    if (theme === 'light') {
      return {
        title: '#0f172a',
        font: '#475569',
        grid: '#e2e8f0',
        line: '#cbd5e1',
        tick: '#94a3b8',
        rangesliderBg: '#f8fafc',
        legendBg: 'rgba(255,255,255,0.92)',
        legendBorder: '#cbd5e1',
        hoverBg: '#ffffff',
        hoverBorder: '#cbd5e1',
        hoverFont: '#0f172a',
        palette: LIGHT_PALETTE,
      };
    }
    return {
      title: '#e2e8f0',
      font: '#94a3b8',
      grid: '#1e293b',
      line: '#334155',
      tick: '#475569',
      rangesliderBg: '#0f172a',
      legendBg: 'rgba(15,23,42,0.8)',
      legendBorder: '#334155',
      hoverBg: '#1e293b',
      hoverBorder: '#475569',
      hoverFont: '#e2e8f0',
      palette: PALETTE,
    };
  }, [theme]);

  const getSeries = useCallback(() => {
    let ids = diseasIds ?? Object.keys(series);
    // Exclude aggregate/summary rows (e.g. D999 "Total")
    ids = ids
      .filter(id => id in series && series[id]?.category !== 'Summary')
      .sort((a, b) => (series[b]?.total_cases ?? 0) - (series[a]?.total_cases ?? 0))
      .slice(0, topN);
    return ids;
  }, [series, diseasIds, topN]);

  const activeIds = getSeries();

  const option = useMemo(() => ({
    backgroundColor: 'transparent',
    title: title
      ? { text: title, textStyle: { color: chartColors.title, fontSize: 15 }, left: 0, top: 0 }
      : undefined,
    tooltip: {
      trigger: 'axis',
      backgroundColor: chartColors.hoverBg,
      borderColor: chartColors.hoverBorder,
      textStyle: { color: chartColors.hoverFont, fontSize: 12 },
      formatter: (params: any[]) => {
        const date = params[0]?.axisValueLabel ?? params[0]?.axisValue ?? '';
        return [
          `<b>${date}</b>`,
          ...params.map(p => `${p.marker}${p.seriesName}: <b>${(p.value?.[1] ?? p.value)?.toLocaleString() ?? 0}</b>`),
        ].join('<br/>');
      },
    },
    legend: {
      top: title ? 30 : 0,
      left: 0,
      orient: 'horizontal' as const,
      textStyle: { color: chartColors.font, fontSize: 11 },
      backgroundColor: chartColors.legendBg,
      borderColor: chartColors.legendBorder,
      borderWidth: 1,
    },
    grid: { left: 55, right: 20, top: title ? 80 : 50, bottom: 60 },
    xAxis: {
      type: 'time' as const,
      axisLabel: { color: chartColors.font, fontSize: 11 },
      axisLine: { lineStyle: { color: chartColors.line } },
      axisTick: { lineStyle: { color: chartColors.tick } },
      splitLine: { lineStyle: { color: chartColors.grid } },
    },
    yAxis: {
      type: 'value' as const,
      name: METRIC_LABELS[metric][lang],
      nameTextStyle: { color: chartColors.font, fontSize: 11 },
      axisLabel: { color: chartColors.font, fontSize: 11 },
      axisLine: { lineStyle: { color: chartColors.line } },
      axisTick: { lineStyle: { color: chartColors.tick } },
      splitLine: { lineStyle: { color: chartColors.grid } },
      min: 0,
    },
    dataZoom: [
      {
        type: 'slider' as const,
        bottom: 5,
        height: 18,
        backgroundColor: chartColors.rangesliderBg,
        borderColor: chartColors.line,
        textStyle: { color: chartColors.font, fontSize: 10 },
        fillerColor: theme === 'light' ? 'rgba(37,99,235,0.15)' : 'rgba(96,165,250,0.15)',
      },
    ],
    series: activeIds.map((id, idx) => {
      const s = series[id];
      const yData = metric === 'incidence_rates' ? s.incidence_rates : s[metric];
      return {
        name: lang === 'zh' ? s.name_zh : s.name_en,
        type: 'line' as const,
        smooth: false,
        showSymbol: false,
        lineStyle: { color: chartColors.palette[idx % chartColors.palette.length], width: 2 },
        itemStyle: { color: chartColors.palette[idx % chartColors.palette.length] },
        data: s.dates.map((d, i) => [d, yData[i] ?? null]),
      };
    }),
  }), [activeIds, series, metric, lang, chartColors, title, theme]);

  return (
    <div className="card p-4">
      {/* Metric switcher */}
      <div className="flex items-center gap-2 mb-4 flex-wrap">
        {(Object.keys(METRIC_LABELS) as Metric[]).map((m) => (
          <button
            key={m}
            onClick={() => setMetric(m)}
            className={`px-3 py-1 text-xs font-medium rounded-full border transition-all ${
              metric === m
                ? 'bg-brand-600 border-brand-500 text-white'
                : 'border-slate-700 text-slate-400 hover:border-slate-600 hover:text-slate-300'
            }`}
          >
            {METRIC_LABELS[m][lang]}
          </button>
        ))}
        <span className="ml-auto text-xs text-slate-600">Top {topN} diseases</span>
      </div>

      {activeIds.length === 0 ? (
        <div className="flex items-center justify-center h-40 text-slate-500 text-sm">No data available</div>
      ) : (
        <EChartsReact
          echarts={echarts}
          option={option}
          notMerge
          style={{ width: '100%', height }}
        />
      )}
    </div>
  );
}
