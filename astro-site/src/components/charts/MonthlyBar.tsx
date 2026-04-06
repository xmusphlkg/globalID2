// src/components/charts/MonthlyBar.tsx
// OWID-style grouped monthly bar chart with raw data preview.

import React, { useState, useMemo, useEffect } from 'react';
import type { CSSProperties } from 'react';
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

interface YearSummary {
  year: string;
  values: number[];
  total: number;
  peakMonth: string;
  peakValue: number;
  color: string;
}

const YEAR_COLORS = ['#0072b2', '#d55e00', '#009e73', '#cc79a7', '#e69f00', '#56b4e9', '#7f3c8d', '#666666', '#bc5090', '#2f4b7c'];
const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function formatCellValue(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) return '—';
  return value.toLocaleString();
}

export default function MonthlyBar({ data, title, height = 380, sourceMeta = null }: Props) {
  const [metric, setMetric] = useState<'cases' | 'deaths'>('cases');
  const [sidebarQuery, setSidebarQuery] = useState('');
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    if (typeof document === 'undefined') return 'light';
    return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  });
  const [lang] = useState<'en' | 'zh'>(() => {
    if (typeof window !== 'undefined') return (localStorage.getItem('lang') as 'en' | 'zh') || 'en';
    return 'en';
  });
  const allYears = useMemo(() => {
    const seenYears = new Set<string>();
    data.months.forEach((ym) => {
      const [year] = ym.split('-');
      if (year) seenYears.add(year);
    });
    return Array.from(seenYears);
  }, [data.months]);
  const [selectedYears, setSelectedYears] = useState<string[]>(allYears);

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
    setSelectedYears(allYears);
    setSidebarQuery('');
  }, [allYears]);

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
      if (!monthName) return;
      if (!byYear[year]) byYear[year] = { months: [], values: [] };
      byYear[year].months.push(monthName);
      byYear[year].values.push(metric === 'cases' ? data.cases[index] : data.deaths[index]);
    });
    return byYear;
  }, [data, metric]);

  const yearColorMap = useMemo(
    () => new Map(allYears.map((year, index) => [year, YEAR_COLORS[index % YEAR_COLORS.length]])),
    [allYears]
  );

  const yearSummaries = useMemo<YearSummary[]>(() => allYears.map((year) => {
    const yearData = grouped[year] ?? { months: [], values: [] };
    const values = MONTH_NAMES.map((monthName) => {
      const monthIndex = yearData.months.indexOf(monthName);
      return monthIndex >= 0 ? yearData.values[monthIndex] : 0;
    });
    const total = values.reduce((sum, value) => sum + value, 0);
    const peakValue = Math.max(0, ...values);
    const peakMonthIndex = values.findIndex((value) => value === peakValue);

    return {
      year,
      values,
      total,
      peakMonth: peakValue > 0 ? (MONTH_NAMES[peakMonthIndex] ?? '—') : '—',
      peakValue,
      color: yearColorMap.get(year) ?? YEAR_COLORS[0],
    };
  }), [allYears, grouped, yearColorMap]);

  const selectedYearSet = useMemo(() => new Set(selectedYears), [selectedYears]);
  const activeYearSummaries = useMemo(
    () => yearSummaries.filter((item) => selectedYearSet.has(item.year)),
    [selectedYearSet, yearSummaries]
  );
  const sidebarYearSummaries = useMemo(() => {
    const query = sidebarQuery.trim().toLowerCase();
    const orderedYears = [...yearSummaries].reverse();

    if (!query) return orderedYears;

    return orderedYears.filter((item) => item.year.toLowerCase().includes(query));
  }, [sidebarQuery, yearSummaries]);
  const yearTotalRange = useMemo(() => {
    if (yearSummaries.length === 0) return { min: 0, max: 0 };
    const totals = yearSummaries.map((item) => item.total);
    return {
      min: Math.min(...totals),
      max: Math.max(...totals),
    };
  }, [yearSummaries]);

  const hasDeathsMetric = useMemo(
    () => data.deaths.some((value) => (value ?? 0) > 0),
    [data.deaths]
  );

  useEffect(() => {
    if (!hasDeathsMetric && metric === 'deaths') {
      setMetric('cases');
    }
  }, [hasDeathsMetric, metric]);

  const metricLabel = lang === 'zh' ? (metric === 'cases' ? '病例数' : '死亡数') : (metric === 'cases' ? 'Cases' : 'Deaths');
  const peakValue = Math.max(0, ...activeYearSummaries.flatMap(({ values }) => values));
  const recentYears = useMemo(() => {
    const sortedYears = [...allYears].sort((left, right) => Number(left) - Number(right));
    return sortedYears.slice(-5);
  }, [allYears]);
  const recentYearSet = useMemo(() => new Set(recentYears), [recentYears]);
  const isShowingAllYears = selectedYearSet.size === allYears.length;
  const isShowingRecentYears = selectedYearSet.size === recentYears.length
    && recentYears.every((year) => selectedYearSet.has(year));

  function toggleYearSelection(year: string) {
    setSelectedYears((current) => (
      current.includes(year)
        ? current.filter((item) => item !== year)
        : allYears.filter((candidate) => candidate === year || current.includes(candidate))
    ));
  }

  function selectAllYears() {
    setSelectedYears(allYears);
  }

  function selectRecentYears() {
    setSelectedYears(allYears.filter((year) => recentYearSet.has(year)));
  }

  function selectVisibleYears() {
    const visibleYears = new Set(sidebarYearSummaries.map((item) => item.year));
    setSelectedYears(allYears.filter((year) => visibleYears.has(year)));
  }

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
    series: activeYearSummaries.map((item) => ({
      name: item.year,
      type: 'bar' as const,
      barMaxWidth: 20,
      itemStyle: { color: item.color, borderRadius: [0, 0, 0, 0] },
      data: item.values,
    })),
    graphic: activeYearSummaries.length === 0
      ? [{
          type: 'text' as const,
          left: 'center',
          top: 'middle',
          silent: true,
          style: {
            text: lang === 'zh'
              ? '当前没有选中的年份\n进入全屏后可在右侧重新勾选年份'
              : 'No years selected\nPick years from the right sidebar in full-screen mode',
            fill: chartColors.font,
            font: '500 14px "Source Sans 3", sans-serif',
            lineHeight: 22,
            textAlign: 'center' as const,
          },
        }]
      : undefined,
  }), [activeYearSummaries, chartColors, lang, metricLabel, theme, title]);

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
      <span className="chart-chip">
        {lang === 'zh'
          ? `显示 ${activeYearSummaries.length} / ${allYears.length} 个年份`
          : `${activeYearSummaries.length}/${allYears.length} years`}
      </span>
      <span className="chart-chip">
        {activeYearSummaries.length > 0
          ? (lang === 'zh' ? `峰值 ${peakValue.toLocaleString()}` : `Peak ${peakValue.toLocaleString()}`)
          : (lang === 'zh' ? '未选择年份' : 'No years selected')}
      </span>
    </>
  );

  const note = activeYearSummaries.length === 0
    ? (lang === 'zh'
        ? '当前没有选中的年份。进入全屏后可在右侧重新勾选年份。'
        : 'There are no years selected right now. Re-enable them from the right-hand filter in full-screen mode.')
    : (lang === 'zh'
        ? '按自然月比较不同年份的总量，适合观察季节性和异常月份。进入全屏后可在右侧筛选年份。'
        : 'Compare calendar-month totals across years to reveal seasonality and unusual peaks. Enter full-screen to filter years from the right-hand sidebar.');

  const legend = (
    <div className="chart-legend">
      {activeYearSummaries.length === 0 ? (
        <div className="chart-sidebar-empty">
          {lang === 'zh' ? '当前没有选中的年份。' : 'No years are selected.'}
        </div>
      ) : activeYearSummaries.map((item) => (
        <div className="chart-legend-item" key={item.year}>
          <div className="chart-legend-row">
            <span
              className="chart-legend-swatch"
              style={{ backgroundColor: item.color }}
            />
            <div>
              <div className="chart-legend-name">{item.year}</div>
              <div className="chart-legend-meta">
                {lang === 'zh' ? '年度合计' : 'Year total'} {item.total.toLocaleString()}
                {' · '}
                {lang === 'zh' ? '峰值' : 'Peak'} {item.peakMonth}
                {item.peakMonth !== '—' ? ` (${formatCellValue(item.peakValue)})` : ''}
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );

  const fullscreenSidebar = (
    <div className="chart-sidebar">
      <div className="chart-sidebar-header">
        <div>
          <div className="chart-sidebar-title">
            {lang === 'zh' ? '年份筛选' : 'Year filter'}
          </div>
          <div className="chart-sidebar-copy">
            {lang === 'zh'
              ? '全屏模式下可搜索并勾选年份，右侧列表会同步更新图表与表格。'
              : 'Search and tick years in full-screen mode. The right-hand list keeps the chart and table in sync.'}
          </div>
        </div>
        <span className="chart-chip whitespace-nowrap">
          {lang === 'zh'
            ? `已选 ${activeYearSummaries.length} / ${allYears.length}`
            : `${activeYearSummaries.length}/${allYears.length} selected`}
        </span>
      </div>

      <input
        type="search"
        value={sidebarQuery}
        onChange={(event) => setSidebarQuery(event.target.value)}
        placeholder={lang === 'zh' ? '搜索年份…' : 'Search years…'}
        className="site-control-input w-full rounded-none border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
      />

      <div className="chart-toolbar">
        <button
          type="button"
          onClick={selectRecentYears}
          className={`chart-toggle ${isShowingRecentYears ? 'chart-toggle-active' : ''}`}
          disabled={recentYears.length === 0}
        >
          {lang === 'zh' ? `最近 ${recentYears.length} 年` : `Last ${recentYears.length} years`}
        </button>
        <button
          type="button"
          onClick={selectAllYears}
          className={`chart-toggle ${isShowingAllYears ? 'chart-toggle-active' : ''}`}
          disabled={allYears.length === 0}
        >
          {lang === 'zh' ? '全部年份' : 'All years'}
        </button>
        <button
          type="button"
          onClick={selectVisibleYears}
          className="chart-toggle"
          disabled={sidebarYearSummaries.length === 0}
        >
          {lang === 'zh' ? '选择当前结果' : 'Select visible'}
        </button>
      </div>

      <div className="chart-sidebar-list">
        {sidebarYearSummaries.length === 0 ? (
          <div className="chart-sidebar-empty">
            {lang === 'zh' ? '没有匹配的年份。' : 'No years matched your search.'}
          </div>
        ) : (
          sidebarYearSummaries.map((item) => {
            const isActive = selectedYearSet.has(item.year);
            const volumePercent = yearTotalRange.max <= yearTotalRange.min
              ? (yearTotalRange.max === 0 ? 0 : 100)
              : ((item.total - yearTotalRange.min) / (yearTotalRange.max - yearTotalRange.min)) * 100;
            const itemStyle = {
              ['--chart-sidebar-volume' as string]: `${Math.max(0, Math.min(100, volumePercent))}%`,
            } as CSSProperties;

            return (
              <label
                key={item.year}
                className={`chart-sidebar-item ${isActive ? 'chart-sidebar-item-active' : ''}`}
                style={itemStyle}
              >
                <div className="chart-sidebar-item-inner flex items-start gap-3">
                  <input
                    type="checkbox"
                    checked={isActive}
                    onChange={() => toggleYearSelection(item.year)}
                    className="chart-sidebar-checkbox mt-1"
                    style={{ accentColor: item.color }}
                  />
                  <span
                    aria-hidden="true"
                    className={`chart-sidebar-swatch ${isActive ? 'chart-sidebar-swatch-active' : ''}`}
                    style={{ backgroundColor: item.color, borderColor: item.color, opacity: isActive ? 1 : 0.55 }}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="chart-sidebar-name">{item.year}</div>
                    <div className="chart-sidebar-meta">
                      {lang === 'zh' ? '年度合计' : 'Year total'} {item.total.toLocaleString()}
                      {' · '}
                      {lang === 'zh' ? '峰值' : 'Peak'} {item.peakMonth}
                      {item.peakMonth !== '—' ? ` (${formatCellValue(item.peakValue)})` : ''}
                    </div>
                  </div>
                </div>
              </label>
            );
          })
        )}
      </div>
    </div>
  );

  const table = (
    <>
      <div className="data-preview-meta">
        {activeYearSummaries.length > 0
          ? (lang === 'zh'
              ? `原始月度数据预览。行表示月份，列表示年份，单元格显示 ${metricLabel}。`
              : `Raw monthly data preview. Rows show calendar months, columns show years, and each cell reports ${metricLabel}.`)
          : (lang === 'zh'
              ? '当前没有选中的年份。进入全屏后可在右侧重新勾选年份。'
              : 'There are no years selected right now. Re-enable them from the right-hand filter in full-screen mode.')}
      </div>
      {activeYearSummaries.length === 0 ? (
        <div className="chart-sidebar-empty">
          {lang === 'zh' ? '暂无可显示的年份列。' : 'There are no year columns to display.'}
        </div>
      ) : (
        <table className="data-preview-table">
          <thead>
            <tr>
              <th className="is-sticky">{lang === 'zh' ? '月份' : 'Month'}</th>
              {activeYearSummaries.map((item) => (
                <th key={item.year}>{item.year}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {MONTH_NAMES.map((monthName, monthIndex) => (
              <tr key={monthName}>
                <td className="is-sticky">{monthName}</td>
                {activeYearSummaries.map((item) => (
                  <td key={`${item.year}-${monthName}`}>{formatCellValue(item.values[monthIndex])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
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
            height: isFullscreen ? '100%' : height,
          }}
        />
      )}
      table={table}
      legend={legend}
      fullscreenSidebar={fullscreenSidebar}
      sourceMeta={sourceMeta}
    />
  );
}
