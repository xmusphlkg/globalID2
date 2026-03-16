// src/components/charts/DiseaseHeatmap.tsx
// ECharts heatmap: diseases (y) × months (x) with log-scaled case counts.

import React, { useEffect, useMemo, useState } from 'react';
import EChartsReact from 'echarts-for-react/lib/core';
import echarts from '../../lib/echarts';

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

  // Flatten z matrix into ECharts [colIndex, rowIndex, value] format
  const heatmapData = useMemo(() => {
    const result: [number, number, number][] = [];
    data.z.forEach((row, rowIdx) => {
      row.forEach((logVal, colIdx) => {
        result.push([colIdx, rowIdx, logVal]);
      });
    });
    return result;
  }, [data.z]);

  const dynamicHeight = Math.max(height, data.disease_labels.length * 18 + 120);

  const option = useMemo(() => ({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'item',
      backgroundColor: chartColors.hoverBg,
      borderColor: chartColors.hoverBorder,
      textStyle: { color: chartColors.hoverFont, fontSize: 12 },
      formatter: (p: any) => {
        const rawCases = Math.round(Math.pow(10, p.value[2]) - 1);
        const disease = data.disease_labels[p.value[1]];
        const month = data.months[p.value[0]];
        return `<b>${disease}</b><br/>${month}<br/>${lang === 'zh' ? '病例数' : 'Cases'}: <b>${rawCases.toLocaleString()}</b>`;
      },
    },
    grid: { left: 200, right: 90, top: 20, bottom: 80 },
    xAxis: {
      type: 'category' as const,
      data: data.months,
      splitArea: { show: false },
      axisLabel: { color: chartColors.font, fontSize: 10, rotate: -45 },
      axisLine: { lineStyle: { color: chartColors.line } },
      axisTick: { lineStyle: { color: chartColors.tick } },
      // Show every 3rd month to avoid crowding
      interval: 2,
    },
    yAxis: {
      type: 'category' as const,
      data: data.disease_labels,
      splitArea: { show: false },
      axisLabel: { color: chartColors.font, fontSize: 11 },
      axisLine: { lineStyle: { color: chartColors.line } },
      axisTick: { lineStyle: { color: chartColors.tick } },
    },
    visualMap: {
      min: 0,
      max: Math.max(...data.z.flat().filter(v => isFinite(v))),
      calculable: false,
      orient: 'vertical' as const,
      right: 10,
      top: 20,
      bottom: 80,
      text: [lang === 'zh' ? '高' : 'High', lang === 'zh' ? '低' : 'Low'],
      textStyle: { color: chartColors.font, fontSize: 10 },
      inRange: {
        color: ['#0f172a', '#1e3a5f', '#1d4ed8', '#0ea5e9', '#14b8a6', '#f59e0b', '#ef4444'],
      },
    },
    series: [{
      type: 'heatmap' as const,
      data: heatmapData,
      emphasis: { itemStyle: { shadowBlur: 10, shadowColor: 'rgba(0,0,0,0.5)' } },
    }],
  }), [data, lang, chartColors, heatmapData]);

  return (
    <div className="card p-4 overflow-x-auto">
      <p className="text-xs text-slate-500 mb-3">
        {lang === 'zh'
          ? '颜色越深代表病例数越多（对数刻度）。暗黑格表示零病例。'
          : 'Darker cells = more cases (log scale). Black = zero cases.'}
      </p>
      <EChartsReact
        echarts={echarts}
        option={option}
        notMerge
        style={{ width: '100%', height: dynamicHeight }}
      />
    </div>
  );
}
