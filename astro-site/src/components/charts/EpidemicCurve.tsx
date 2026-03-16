// src/components/charts/EpidemicCurve.tsx
// Interactive Plotly time-series chart for disease cases/deaths/incidence rates.
// Accepts pre-structured trace data from the Python export.

import React, { useState, useCallback, useEffect, useMemo } from 'react';
import Plot from 'react-plotly.js';
import type { Data, Layout } from 'plotly.js';

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

  const traces: Data[] = getSeries().map((id, idx) => {
    const s = series[id];
    const yData = metric === 'incidence_rates' ? s.incidence_rates : s[metric];
    return {
      type: 'scatter',
      mode: 'lines',
      name: lang === 'zh' ? s.name_zh : s.name_en,
      x: s.dates,
      y: yData as number[],
      line: {
        color: chartColors.palette[idx % chartColors.palette.length],
        width: 2,
      },
      hovertemplate: `<b>%{fullData.name}</b><br>%{x}<br>${METRIC_LABELS[metric][lang]}: <b>%{y:,}</b><extra></extra>`,
    };
  });

  const layout: Partial<Layout> = {
    title: title
      ? { text: title, font: { color: chartColors.title, size: 15 }, x: 0, pad: { l: 0 } }
      : undefined,
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: { family: 'Inter, system-ui, sans-serif', color: chartColors.font, size: 12 },
    height,
    margin: { l: 55, r: 20, t: 60, b: 40 },
    xaxis: {
      gridcolor: chartColors.grid,
      linecolor: chartColors.line,
      tickcolor: chartColors.tick,
      tickfont: { size: 11, color: chartColors.font },
      rangeslider: { visible: true, bgcolor: chartColors.rangesliderBg, thickness: 0.06 },
      type: 'date',
    },
    yaxis: {
      gridcolor: chartColors.grid,
      linecolor: chartColors.line,
      tickcolor: chartColors.tick,
      tickfont: { size: 11, color: chartColors.font },
      title: { text: METRIC_LABELS[metric][lang], font: { size: 11 } },
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
      y: 1.02,
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

      {traces.length === 0 ? (
        <div className="flex items-center justify-center h-40 text-slate-500 text-sm">No data available</div>
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
