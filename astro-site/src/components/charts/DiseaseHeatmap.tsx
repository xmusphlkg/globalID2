// src/components/charts/DiseaseHeatmap.tsx
// Plotly heatmap: diseases (y) × months (x) with log-scaled case counts.

import React, { useEffect, useMemo, useState } from 'react';
import Plot from 'react-plotly.js';
import type { Data, Layout } from 'plotly.js';

interface HeatmapData {
  diseases: string[];
  disease_labels: string[];
  months: string[];
  z: number[][];
}

interface Props {
  data: HeatmapData;
  height?: number;
}

export default function DiseaseHeatmap({ data, height = 600 }: Props) {
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
        font: '#475569',
        grid: '#e2e8f0',
        line: '#cbd5e1',
        tick: '#94a3b8',
        colorbarBorder: '#cbd5e1',
        hoverBg: '#ffffff',
        hoverBorder: '#cbd5e1',
        hoverFont: '#0f172a',
      };
    }
    return {
      font: '#94a3b8',
      grid: '#1e293b',
      line: '#334155',
      tick: '#475569',
      colorbarBorder: '#334155',
      hoverBg: '#1e293b',
      hoverBorder: '#475569',
      hoverFont: '#e2e8f0',
    };
  }, [theme]);

  if (!data || !data.z || data.z.length === 0) {
    return (
      <div className="card p-8 flex items-center justify-center text-slate-500 text-sm">
        {lang === 'zh' ? '暂无数据' : 'No data available'}
      </div>
    );
  }

  // Custom hover text: convert log scale back to raw cases
  const customdata = data.z.map((row) =>
    row.map((logVal) => Math.round(Math.pow(10, logVal) - 1))
  );

  const traces: Data[] = [
    {
      type: 'heatmap',
      x: data.months,
      y: data.disease_labels,
      z: data.z,
      customdata,
      colorscale: [
        [0, '#0f172a'],
        [0.1, '#1e3a5f'],
        [0.3, '#1d4ed8'],
        [0.5, '#0ea5e9'],
        [0.7, '#14b8a6'],
        [0.85, '#f59e0b'],
        [1, '#ef4444'],
      ] as unknown as string,
      zmin: 0,
      zsmooth: false,
      hoverongaps: false,
      hovertemplate:
        '<b>%{y}</b><br>%{x}<br>' +
        (lang === 'zh' ? '病例数' : 'Cases') +
        ': <b>%{customdata:,}</b><extra></extra>',
      colorbar: {
        title: {
          text: lang === 'zh' ? 'log₁₀(病例+1)' : 'log₁₀(cases+1)',
          font: { color: chartColors.font, size: 11 },
          side: 'right',
        },
        tickfont: { color: chartColors.font, size: 10 },
        outlinecolor: chartColors.colorbarBorder,
        bordercolor: chartColors.colorbarBorder,
        bgcolor: 'rgba(0,0,0,0)',
        thickness: 12,
      },
    },
  ];

  const layout: Partial<Layout> = {
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: { family: 'Inter, system-ui, sans-serif', color: chartColors.font, size: 11 },
    height: Math.max(height, data.disease_labels.length * 18 + 100),
    margin: { l: 200, r: 80, t: 20, b: 80 },
    xaxis: {
      gridcolor: chartColors.grid,
      linecolor: chartColors.line,
      tickcolor: chartColors.tick,
      tickangle: -45,
      tickfont: { size: 10, color: chartColors.font },
      // Show every 3rd month label to avoid crowding
      dtick: 'M3',
      tickformat: "%b '%y",
      type: 'date' as const,
    },
    yaxis: {
      gridcolor: chartColors.grid,
      linecolor: chartColors.line,
      tickcolor: chartColors.tick,
      tickfont: { size: 11, color: chartColors.font },
      automargin: true,
    },
    hovermode: 'closest',
    hoverlabel: {
      bgcolor: chartColors.hoverBg,
      bordercolor: chartColors.hoverBorder,
      font: { color: chartColors.hoverFont, size: 12 },
    },
  };

  return (
    <div className="card p-4 overflow-x-auto">
      <p className="text-xs text-slate-500 mb-3">
        {lang === 'zh'
          ? '颜色越深代表病例数越多（对数刻度）。暗黑格表示零病例。'
          : 'Darker cells = more cases (log scale). Black = zero cases.'}
      </p>
      <Plot
        data={traces}
        layout={layout}
        config={{ displayModeBar: 'hover', responsive: true, modeBarButtonsToRemove: ['lasso2d', 'select2d'] }}
        style={{ width: '100%' }}
        useResizeHandler
      />
    </div>
  );
}
