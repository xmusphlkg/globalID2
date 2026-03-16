// src/components/charts/MonthlyBar.tsx
// ECharts bar chart showing monthly case/death counts for a single disease,
// with year-over-year grouped bars for comparison.

import React, { useState, useMemo, useEffect } from 'react';
import EChartsReact from 'echarts-for-react/lib/core';
import echarts from '../../lib/echarts';

interface MonthlyData {
  months: string[];   // "YYYY-MM" strings
  cases: number[];
  deaths: number[];
}

interface Props {
  data: MonthlyData;
  title?: string;
  height?: number;
}

const YEAR_COLORS = [
  '#60a5fa', '#34d399', '#f472b6', '#fb923c',
  '#a78bfa', '#38bdf8', '#4ade80', '#fbbf24',
];

const YEAR_COLORS_LIGHT = [
  '#2563eb', '#0d9488', '#db2777', '#ea580c',
  '#7c3aed', '#0284c7', '#16a34a', '#ca8a04',
];

const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export default function MonthlyBar({ data, title, height = 380 }: Props) {
  const [metric, setMetric] = useState<'cases' | 'deaths'>('cases');
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
    const updateTheme = () => setTheme(root.getAttribute('data-theme') === 'light' ? 'light' : 'dark');
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
        legendBg: 'rgba(255,255,255,0.92)',
        legendBorder: '#cbd5e1',
        hoverBg: '#ffffff',
        hoverBorder: '#cbd5e1',
        hoverFont: '#0f172a',
        palette: YEAR_COLORS_LIGHT,
      };
    }
    return {
      title: '#e2e8f0',
      font: '#94a3b8',
      grid: '#1e293b',
      line: '#334155',
      tick: '#475569',
      legendBg: 'rgba(15,23,42,0.8)',
      legendBorder: '#334155',
      hoverBg: '#1e293b',
      hoverBorder: '#475569',
      hoverFont: '#e2e8f0',
      palette: YEAR_COLORS,
    };
  }, [theme]);

  // Group by year → each year is one bar group
  const grouped = useMemo(() => {
    const byYear: Record<string, { months: string[]; values: number[] }> = {};
    data.months.forEach((ym, i) => {
      const [year, monthIdx] = ym.split('-');
      const monthName = MONTH_NAMES[parseInt(monthIdx, 10) - 1];
      if (!byYear[year]) byYear[year] = { months: [], values: [] };
      byYear[year].months.push(monthName);
      byYear[year].values.push(metric === 'cases' ? data.cases[i] : data.deaths[i]);
    });
    return byYear;
  }, [data, metric]);

  const metricLabel = lang === 'zh' ? (metric === 'cases' ? '病例数' : '死亡数') : (metric === 'cases' ? 'Cases' : 'Deaths');

  const option = useMemo(() => ({
    backgroundColor: 'transparent',
    title: title ? { text: title, textStyle: { color: chartColors.title, fontSize: 14 }, left: 0, top: 0 } : undefined,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: chartColors.hoverBg,
      borderColor: chartColors.hoverBorder,
      textStyle: { color: chartColors.hoverFont, fontSize: 12 },
      formatter: (params: any[]) =>
        params.map(p => `<b>${p.seriesName}</b> - ${p.name}<br/>${metricLabel}: <b>${p.value?.toLocaleString() ?? 0}</b>`).join('<br/>'),
    },
    legend: {
      bottom: 0,
      left: 0,
      orient: 'horizontal' as const,
      textStyle: { color: chartColors.font, fontSize: 11 },
      backgroundColor: chartColors.legendBg,
      borderColor: chartColors.legendBorder,
      borderWidth: 1,
    },
    grid: { left: 55, right: 20, top: title ? 45 : 15, bottom: 60 },
    xAxis: {
      type: 'category' as const,
      data: MONTH_NAMES,
      axisLabel: { color: chartColors.font, fontSize: 11 },
      axisLine: { lineStyle: { color: chartColors.line } },
      axisTick: { lineStyle: { color: chartColors.tick } },
      splitLine: { lineStyle: { color: chartColors.grid } },
    },
    yAxis: {
      type: 'value' as const,
      name: metricLabel,
      nameTextStyle: { color: chartColors.font, fontSize: 11 },
      axisLabel: { color: chartColors.font, fontSize: 11 },
      axisLine: { lineStyle: { color: chartColors.line } },
      axisTick: { lineStyle: { color: chartColors.tick } },
      splitLine: { lineStyle: { color: chartColors.grid } },
      min: 0,
    },
    series: Object.entries(grouped).map(([year, { months, values }], idx) => ({
      name: year,
      type: 'bar' as const,
      data: MONTH_NAMES.map(mon => {
        const i = months.indexOf(mon);
        return i >= 0 ? values[i] : 0;
      }),
      itemStyle: { color: chartColors.palette[idx % chartColors.palette.length], opacity: 0.85 },
    })),
  }), [grouped, chartColors, title, metricLabel]);

  const hasData = Object.keys(grouped).length > 0;

  return (
    <div className="card p-4">
      {/* Metric switcher */}
      <div className="flex items-center gap-2 mb-4">
        {(['cases', 'deaths'] as const).map((m) => (
          <button
            key={m}
            onClick={() => setMetric(m)}
            className={`px-3 py-1 text-xs font-medium rounded-full border transition-all ${
              metric === m
                ? 'bg-brand-600 border-brand-500 text-white'
                : 'border-slate-700 text-slate-400 hover:border-slate-600 hover:text-slate-300'
            }`}
          >
            {m === 'cases'
              ? (lang === 'zh' ? '病例数' : 'Cases')
              : (lang === 'zh' ? '死亡数' : 'Deaths')}
          </button>
        ))}
      </div>

      {!hasData ? (
        <div className="flex items-center justify-center h-40 text-slate-500 text-sm">
          {lang === 'zh' ? '暂无数据' : 'No data available'}
        </div>
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
