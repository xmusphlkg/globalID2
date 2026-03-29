// src/components/charts/MonthlyBar.tsx
// OWID-style grouped monthly bar chart with raw data preview.

import React, { useState, useMemo, useEffect } from 'react';
import EChartsReact from 'echarts-for-react/lib/core';
import echarts from '../../lib/echarts';
import ChartFrame from './ChartFrame';
import type { ChartSourceMeta } from '../../utils/chartMeta';

interface MonthlyData {
  months: string[];
  cases: number[];
  deaths: number[];
}

interface Props {
  data: MonthlyData;
  title?: string;
  height?: number;
  sourceMeta?: ChartSourceMeta | null;
}

const YEAR_COLORS = ['#0072b2', '#d55e00', '#009e73', '#cc79a7', '#e69f00', '#56b4e9', '#7f3c8d', '#666666', '#bc5090', '#2f4b7c'];
const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function formatCellValue(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return '—';
  return value.toLocaleString();
}

export default function MonthlyBar({ data, title, height = 380, sourceMeta = null }: Props) {
  const [metric, setMetric] = useState<'cases' | 'deaths'>('cases');
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
          grid: '#d9d2c7',
          hoverBg: '#fffdfa',
          hoverBorder: '#c9c2b8',
          hoverFont: '#162232',
        }
      : {
          font: '#a4b1c1',
          line: '#51667f',
          grid: '#344a64',
          hoverBg: '#17283a',
          hoverBorder: '#51667f',
          hoverFont: '#f3f6fb',
        }
  ), [theme]);

  const grouped = useMemo(() => {
    const byYear: Record<string, { months: string[]; values: number[] }> = {};
    data.months.forEach((ym, index) => {
      const [year, monthIdx] = ym.split('-');
      const monthName = MONTH_NAMES[parseInt(monthIdx, 10) - 1];
      if (!byYear[year]) byYear[year] = { months: [], values: [] };
      byYear[year].months.push(monthName);
      byYear[year].values.push(metric === 'cases' ? data.cases[index] : data.deaths[index]);
    });
    return byYear;
  }, [data, metric]);

  const hasDeathsMetric = useMemo(
    () => data.deaths.some((value) => (value ?? 0) > 0),
    [data.deaths]
  );

  useEffect(() => {
    if (!hasDeathsMetric && metric === 'deaths') {
      setMetric('cases');
    }
  }, [hasDeathsMetric, metric]);

  const years = Object.keys(grouped);
  const metricLabel = lang === 'zh' ? (metric === 'cases' ? '病例数' : '死亡数') : (metric === 'cases' ? 'Cases' : 'Deaths');
  const peakValue = Math.max(0, ...Object.values(grouped).flatMap(({ values }) => values));

  const option = useMemo(() => ({
    animationDuration: 240,
    backgroundColor: 'transparent',
    title: title
      ? { text: title, textStyle: { color: theme === 'light' ? '#162232' : '#f3f6fb', fontSize: 15, fontWeight: 600 }, left: 0, top: 0 }
      : undefined,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' as const },
      backgroundColor: chartColors.hoverBg,
      borderColor: chartColors.hoverBorder,
      borderWidth: 1,
      textStyle: { color: chartColors.hoverFont, fontSize: 12 },
      formatter: (params: any[]) =>
        params
          .map((param) => `<b>${param.seriesName}</b> · ${param.name}<br/>${metricLabel}: <b>${formatCellValue(param.value)}</b>`)
          .join('<br/>'),
    },
    grid: { left: 60, right: 18, top: title ? 38 : 16, bottom: 48 },
    xAxis: {
      type: 'category' as const,
      data: MONTH_NAMES,
      axisLabel: { color: chartColors.font, fontSize: 11 },
      axisLine: { lineStyle: { color: chartColors.line } },
      axisTick: { lineStyle: { color: chartColors.line } },
    },
    yAxis: {
      type: 'value' as const,
      name: metricLabel,
      nameTextStyle: { color: chartColors.font, fontSize: 11, padding: [0, 0, 8, 0] },
      axisLabel: { color: chartColors.font, fontSize: 11 },
      axisLine: { lineStyle: { color: chartColors.line } },
      axisTick: { lineStyle: { color: chartColors.line } },
      splitLine: { lineStyle: { color: chartColors.grid, type: 'dashed' as const } },
      min: 0,
    },
    series: years.map((year, index) => ({
      name: year,
      type: 'bar' as const,
      barMaxWidth: 20,
      itemStyle: { color: YEAR_COLORS[index % YEAR_COLORS.length], borderRadius: [0, 0, 0, 0] },
      data: MONTH_NAMES.map((monthName) => {
        const monthIndex = grouped[year].months.indexOf(monthName);
        return monthIndex >= 0 ? grouped[year].values[monthIndex] : 0;
      }),
    })),
  }), [chartColors, grouped, lang, metricLabel, theme, title, years]);

  const toolbar = (
    <>
      <div className="chart-toolbar">
        {(['cases', 'deaths'] as const).filter((candidate) => candidate !== 'deaths' || hasDeathsMetric).map((candidate) => (
          <button
            key={candidate}
            type="button"
            onClick={() => setMetric(candidate)}
            className={`chart-toggle ${metric === candidate ? 'chart-toggle-active' : ''}`}
          >
            {candidate === 'cases'
              ? (lang === 'zh' ? '病例数' : 'Cases')
              : (lang === 'zh' ? '死亡数' : 'Deaths')}
          </button>
        ))}
      </div>
      <span className="chart-chip">{lang === 'zh' ? `${years.length} 个年份` : `${years.length} years`}</span>
      <span className="chart-chip">{lang === 'zh' ? `峰值 ${peakValue.toLocaleString()}` : `Peak ${peakValue.toLocaleString()}`}</span>
    </>
  );

  const note = lang === 'zh'
    ? '按自然月比较不同年份的总量，适合观察季节性和异常月份。图例已从绘图区移出，避免遮挡柱体。'
    : 'Compare calendar-month totals across years to reveal seasonality and unusual peaks. The legend is kept outside the plotting area.';

  const legend = (
    <div className="chart-legend">
      {years.map((year, index) => {
        const values = MONTH_NAMES.map((monthName) => {
          const monthIndex = grouped[year].months.indexOf(monthName);
          return monthIndex >= 0 ? grouped[year].values[monthIndex] : 0;
        });
        const yearlyTotal = values.reduce((sum, value) => sum + value, 0);
        return (
          <div className="chart-legend-item" key={year}>
            <div className="chart-legend-row">
              <span
                className="chart-legend-swatch"
                style={{ backgroundColor: YEAR_COLORS[index % YEAR_COLORS.length] }}
              />
              <div>
                <div className="chart-legend-name">{year}</div>
                <div className="chart-legend-meta">
                  {lang === 'zh' ? '年度合计' : 'Year total'} {yearlyTotal.toLocaleString()}
                  {' · '}
                  {lang === 'zh' ? '峰值月' : 'Peak month'} {MONTH_NAMES[values.indexOf(Math.max(...values))]}
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );

  const table = (
    <>
      <div className="data-preview-meta">
        {lang === 'zh'
          ? `原始月度数据预览。行表示月份，列表示年份，单元格显示 ${metricLabel}。`
          : `Raw monthly data preview. Rows show calendar months, columns show years, and each cell reports ${metricLabel}.`}
      </div>
      <table className="data-preview-table">
        <thead>
          <tr>
            <th className="is-sticky">{lang === 'zh' ? '月份' : 'Month'}</th>
            {years.map((year) => (
              <th key={year}>{year}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {MONTH_NAMES.map((monthName) => (
            <tr key={monthName}>
              <td className="is-sticky">{monthName}</td>
              {years.map((year) => {
                const monthIndex = grouped[year].months.indexOf(monthName);
                const value = monthIndex >= 0 ? grouped[year].values[monthIndex] : null;
                return <td key={`${year}-${monthName}`}>{formatCellValue(value)}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );

  return (
    <ChartFrame
      lang={lang}
      toolbar={toolbar}
      note={note}
      chart={({ isFullscreen }) => (
        <EChartsReact
          echarts={echarts}
          option={option}
          notMerge
          style={{
            width: '100%',
            height: isFullscreen ? 'clamp(500px, calc(100vh - 250px), 820px)' : height,
          }}
        />
      )}
      table={table}
      legend={legend}
      sourceMeta={sourceMeta}
    />
  );
}
