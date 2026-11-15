import { useMemo } from 'react';
import EChartsReact from '../../../lib/echartsReact';
import echarts from '../../../lib/echarts';
import { type DiseaseSeries } from './types.ts';
import { ui, chartTokens, fmtNum } from './presentation.ts';

export const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export const YEAR_PALETTE_DARK = [
  '#60a5fa', '#34d399', '#f472b6', '#fb923c', '#a78bfa',
  '#38bdf8', '#4ade80', '#fbbf24', '#e879f9', '#f87171',
  '#22d3ee', '#86efac',
];

export const YEAR_PALETTE_LIGHT = [
  '#2563eb', '#0d9488', '#db2777', '#ea580c', '#7c3aed',
  '#0284c7', '#16a34a', '#ca8a04', '#be185d', '#dc2626',
  '#0891b2', '#65a30d',
];

export function MonthlyDistributionChart({ series, metric, theme, lang }: {
  series: DiseaseSeries;
  metric: 'cases' | 'deaths';
  theme: 'light' | 'dark';
  lang: 'en' | 'zh' | 'fr';
}) {
  const t = chartTokens(theme);
  const palette = theme === 'light' ? YEAR_PALETTE_LIGHT : YEAR_PALETTE_DARK;
  const values = metric === 'cases' ? series.cases : series.deaths;
  const label = metric === 'cases'
    ? ui(lang, 'Cases', '病例数', 'Cas')
    : ui(lang, 'Deaths', '死亡数', 'Décès');

  const grouped = useMemo(() => {
    const byYear: Record<string, (number | null)[]> = {};
    series.dates.forEach((d, i) => {
      const [year, mon] = d.substring(0, 7).split('-');
      const monIdx = parseInt(mon, 10) - 1;
      if (!byYear[year]) byYear[year] = new Array(12).fill(null);
      if (values[i] != null) byYear[year][monIdx] = (byYear[year][monIdx] ?? 0) + values[i]!;
    });
    return byYear;
  }, [series, values]);

  const years = Object.keys(grouped).sort();

  const option = useMemo(() => ({
    backgroundColor: t.bg,
    tooltip: {
      trigger: 'axis' as const,
      axisPointer: { type: 'shadow' as const },
      backgroundColor: t.tooltipBg, borderColor: t.tooltipBorder, borderWidth: 1,
      textStyle: { color: t.tooltipFont, fontSize: 12 },
    },
    legend: {
      bottom: 0, left: 0, orient: 'horizontal' as const,
      textStyle: { color: t.font, fontSize: 10 },
      itemWidth: 12, itemHeight: 8,
      backgroundColor: t.legendBg, borderColor: t.legendBorder, borderWidth: 1,
      padding: [3, 6], borderRadius: 0,
    },
    grid: { left: 50, right: 12, top: 12, bottom: 68 },
    xAxis: {
      type: 'category' as const,
      data: MONTH_NAMES,
      axisLabel: { color: t.font, fontSize: 10 },
      axisLine: { lineStyle: { color: t.line } },
      axisTick: { lineStyle: { color: t.tick } },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value' as const,
      name: label,
      nameTextStyle: { color: t.font, fontSize: 10 },
      axisLabel: { color: t.font, fontSize: 10, formatter: fmtNum },
      axisLine: { lineStyle: { color: t.line } },
      splitLine: { lineStyle: { color: t.grid } },
      min: 0,
    },
    series: years.map((year, idx) => ({
      name: year,
      type: 'bar' as const,
      barGap: '10%',
      data: grouped[year],
      itemStyle: { color: palette[idx % palette.length], opacity: 0.85 },
      emphasis: { itemStyle: { opacity: 1 } },
    })),
  }), [grouped, years, theme, lang, t, label, palette]);

  if (years.length === 0) return null;

  return (
    <EChartsReact
      echarts={echarts}
      option={option}
      notMerge
      style={{ width: '100%', height: 300 }}
    />
  );
}
