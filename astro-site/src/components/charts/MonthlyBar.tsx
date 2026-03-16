// src/components/charts/MonthlyBar.tsx
// Plotly bar chart showing monthly case/death counts for a single disease,
// with year-over-year grouped bars for comparison.

import React, { useState, useMemo, useEffect } from 'react';
import Plot from 'react-plotly.js';
import type { Data, Layout } from 'plotly.js';

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

  const traces: Data[] = Object.entries(grouped).map(([year, { months, values }], idx) => ({
    type: 'bar',
    name: year,
    x: months,
    y: values,
    marker: { color: chartColors.palette[idx % chartColors.palette.length], opacity: 0.85 },
    hovertemplate: `<b>${year}</b> - %{x}<br>${lang === 'zh' ? (metric === 'cases' ? '病例数' : '死亡数') : (metric === 'cases' ? 'Cases' : 'Deaths')}: <b>%{y:,}</b><extra></extra>`,
  }));

  const layout: Partial<Layout> = {
    title: title
      ? { text: title, font: { color: chartColors.title, size: 14 }, x: 0, pad: { l: 0 } }
      : undefined,
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: { family: 'Inter, system-ui, sans-serif', color: chartColors.font, size: 12 },
    height,
    margin: { l: 55, r: 20, t: title ? 45 : 15, b: 50 },
    barmode: 'group',
    bargap: 0.15,
    bargroupgap: 0.05,
    xaxis: {
      gridcolor: chartColors.grid,
      linecolor: chartColors.line,
      tickcolor: chartColors.tick,
      tickfont: { size: 11, color: chartColors.font },
      categoryarray: MONTH_NAMES,
    },
    yaxis: {
      gridcolor: chartColors.grid,
      linecolor: chartColors.line,
      tickcolor: chartColors.tick,
      tickfont: { size: 11, color: chartColors.font },
      title: {
        text: lang === 'zh'
          ? (metric === 'cases' ? '病例数' : '死亡数')
          : (metric === 'cases' ? 'Cases' : 'Deaths'),
        font: { size: 11, color: chartColors.font },
      },
      rangemode: 'tozero',
    },
    legend: {
      bgcolor: chartColors.legendBg,
      bordercolor: chartColors.legendBorder,
      borderwidth: 1,
      font: { size: 11, color: chartColors.font },
      orientation: 'h',
      xanchor: 'left',
      yanchor: 'bottom',
      y: -0.3,
      x: 0,
    },
    hovermode: 'closest',
    hoverlabel: {
      bgcolor: chartColors.hoverBg,
      bordercolor: chartColors.hoverBorder,
      font: { color: chartColors.hoverFont, size: 12 },
    },
  };

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

      {traces.length === 0 ? (
        <div className="flex items-center justify-center h-40 text-slate-500 text-sm">
          {lang === 'zh' ? '暂无数据' : 'No data available'}
        </div>
      ) : (
        <Plot
          data={traces}
          layout={layout}
          config={{ displayModeBar: 'hover', responsive: true, modeBarButtonsToRemove: ['lasso2d', 'select2d'] }}
          style={{ width: '100%', height }}
          useResizeHandler
        />
      )}
    </div>
  );
}
