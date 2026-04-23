// src/components/charts/DiseaseReportPanel.tsx
// OWID-inspired disease appendix cards for report pages.

import React, { useEffect, useMemo, useState } from 'react';
import EChartsReact from 'echarts-for-react/lib/core';
import echarts from '../../lib/echarts';
import ChartFrame from './ChartFrame';
import type { ChartSourceMeta } from '../../utils/chartMeta';

export interface DiseaseSection {
  section_type: string;
  title: string;
  content: string;
  content_html?: string;
}

export interface DiseaseReportData {
  disease_id: string;
  name_en: string;
  name_zh: string;
  category: string;
  slug: string;
  cases: number;
  deaths: number;
  cases_prev_month: number;
  cases_prev_year: number;
  sections: DiseaseSection[];
  series?: {
    dates: string[];
    cases: number[];
    deaths: number[];
    incidence_rates: (number | null)[];
    total_cases: number;
  };
}

interface Props {
  diseases: DiseaseReportData[];
  countryCode: string;
  reportId: number | string;
  sourceMeta?: ChartSourceMeta | null;
}

const CATEGORY_COLORS: Record<string, string> = {
  Viral: 'bg-blue-500/12 text-blue-300 ring-1 ring-blue-500/20',
  Bacterial: 'bg-amber-500/12 text-amber-300 ring-1 ring-amber-500/20',
  Parasitic: 'bg-green-500/12 text-green-300 ring-1 ring-green-500/20',
  Fungal: 'bg-violet-500/12 text-violet-300 ring-1 ring-violet-500/20',
};

const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const YEAR_COLORS = ['#0072b2', '#d55e00', '#009e73', '#cc79a7', '#e69f00', '#56b4e9', '#7f3c8d', '#666666'];
const TREND_METRICS = {
  cases: {
    color: '#0072b2',
    fill: 'rgba(0,114,178,0.14)',
    dash: 'solid' as const,
    en: 'Cases',
    zh: '病例数',
  },
  deaths: {
    color: '#d55e00',
    fill: 'rgba(213,94,0,0.14)',
    dash: 'dashed' as const,
    en: 'Deaths',
    zh: '死亡数',
  },
  incidence_rates: {
    color: '#009e73',
    fill: 'rgba(0,158,115,0.12)',
    dash: 'dotted' as const,
    en: 'Incidence rate (per 100k)',
    zh: '发病率（每10万）',
  },
};

function useTheme() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    if (typeof document === 'undefined') return 'light';
    return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  });

  useEffect(() => {
    if (typeof document === 'undefined') return;
    const root = document.documentElement;
    const update = () => setTheme(root.getAttribute('data-theme') === 'light' ? 'light' : 'dark');
    update();
    const observer = new MutationObserver(update);
    observer.observe(root, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);

  return theme;
}

function useLang() {
  const [lang, setLang] = useState<'en' | 'zh'>(() => {
    if (typeof window === 'undefined') return 'en';
    return (localStorage.getItem('lang') as 'en' | 'zh') || 'en';
  });

  useEffect(() => {
    const handler = () => setLang((localStorage.getItem('lang') as 'en' | 'zh') || 'en');
    window.addEventListener('storage', handler);
    return () => window.removeEventListener('storage', handler);
  }, []);

  return lang;
}

function getChartColors(theme: 'light' | 'dark') {
  return theme === 'light'
    ? {
        font: '#556070',
        line: '#c9c2b8',
        grid: '#d9d2c7',
        hoverBg: '#fffdfa',
        hoverBorder: '#c9c2b8',
        hoverFont: '#162232',
        sliderBg: '#f7f3ec',
      }
    : {
        font: '#a4b1c1',
        line: '#51667f',
        grid: '#344a64',
        hoverBg: '#17283a',
        hoverBorder: '#51667f',
        hoverFont: '#f3f6fb',
        sliderBg: '#122132',
      };
}

function formatValue(value: number | null | undefined, digits = 0) {
  if (value == null || Number.isNaN(value)) return '—';
  return digits > 0 ? value.toFixed(digits) : value.toLocaleString();
}

function stripMarkdown(raw: string) {
  return raw
    .replace(/!\[[^\]]*]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)]\([^)]*\)/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/^\s*\d+\.\s+/gm, '')
    .replace(/[`*_>]/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function sectionLabel(sectionType: string, lang: 'en' | 'zh') {
  const labels: Record<string, { en: string; zh: string }> = {
    executive_summary: { en: 'Executive summary', zh: '执行摘要' },
    disease_card: { en: 'Disease card', zh: '疾病卡片' },
    summary: { en: 'Summary', zh: '摘要' },
    key_findings: { en: 'Key findings', zh: '关键发现' },
    trend_analysis: { en: 'Trend analysis', zh: '趋势分析' },
    highlights: { en: 'Highlights', zh: '要点' },
    data_quality_notes: { en: 'Data quality', zh: '数据质量' },
    methodology: { en: 'Methodology', zh: '方法说明' },
  };
  return labels[sectionType]?.[lang] ?? sectionType.replaceAll('_', ' ');
}

function sectionOrder(sectionType: string) {
  const order = ['disease_card', 'summary', 'key_findings', 'trend_analysis', 'highlights'];
  const index = order.indexOf(sectionType);
  return index === -1 ? order.length : index;
}

function hasStructuredCard(disease: DiseaseReportData) {
  return disease.sections.some((section) => section.section_type === 'disease_card');
}

function Delta({ current, prev }: { current: number; prev: number }) {
  if (prev === 0 && current === 0) return <span className="text-slate-600">—</span>;
  const diff = current - prev;
  const pct = prev > 0 ? ((Math.abs(diff) / prev) * 100).toFixed(0) : '∞';
  if (diff === 0) return <span className="text-slate-500 text-xs">=</span>;
  return diff > 0
    ? <span className="text-red-400 text-xs">▲{pct}%</span>
    : <span className="text-emerald-400 text-xs">▼{pct}%</span>;
}

function AnalysisSummary({ sections, lang }: { sections: DiseaseSection[]; lang: 'en' | 'zh' }) {
  const orderedSections = useMemo(() => {
    const sorted = [...sections].sort((a, b) => sectionOrder(a.section_type) - sectionOrder(b.section_type));
    const diseaseCards = sorted.filter((section) => section.section_type === 'disease_card');
    return diseaseCards.length > 0 ? diseaseCards.slice(0, 1) : sorted.slice(0, 3);
  }, [sections]);

  if (orderedSections.length === 0) return null;

  return (
    <div className="space-y-3">
      <h4 className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">
        {lang === 'zh' ? '文本摘要' : 'Narrative summary'}
      </h4>
      <div className={`grid gap-4 ${orderedSections.some((section) => section.section_type === 'disease_card') ? 'lg:grid-cols-1' : 'lg:grid-cols-3'}`}>
        {orderedSections.map((section) => {
          const isDiseaseCard = section.section_type === 'disease_card';
          const paragraphs = stripMarkdown(section.content)
            .split(/\n{2,}/)
            .map((paragraph) => paragraph.trim())
            .filter(Boolean)
            .slice(0, isDiseaseCard ? 10 : 2);

          return (
            <article key={`${section.section_type}-${section.title}`} className="border border-slate-700/60 bg-slate-800/10 p-4">
              <div className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                {sectionLabel(section.section_type, lang)}
              </div>
              <div className="space-y-3">
                {paragraphs.length > 0 ? paragraphs.map((paragraph, index) => (
                  <p key={index} className={`text-sm leading-6 ${isDiseaseCard && index === 0 ? 'font-semibold text-slate-200' : 'text-slate-400'}`}>
                    {paragraph}
                  </p>
                )) : (
                  <p className="text-sm leading-6 text-slate-500">
                    {lang === 'zh' ? '暂无分析摘要。' : 'No narrative summary available.'}
                  </p>
                )}
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}

function TrendChart({
  series,
  height,
  theme,
  lang,
  sourceMeta,
}: {
  series: DiseaseReportData['series'];
  height: number;
  theme: 'light' | 'dark';
  lang: 'en' | 'zh';
  sourceMeta?: ChartSourceMeta | null;
}) {
  const [metric, setMetric] = useState<'cases' | 'deaths' | 'incidence_rates'>('cases');
  const cc = getChartColors(theme);
  const metricConfig = TREND_METRICS[metric];
  const safeSeries = series ?? {
    dates: [],
    cases: [],
    deaths: [],
    incidence_rates: [],
    total_cases: 0,
  };
  const currentValues = metric === 'cases'
    ? safeSeries.cases
    : metric === 'deaths'
      ? safeSeries.deaths
      : safeSeries.incidence_rates;
  const latestValue = [...currentValues].reverse().find((value) => value != null) ?? null;
  const startDate = safeSeries.dates[0];
  const endDate = safeSeries.dates[safeSeries.dates.length - 1];
  const hasDeathsMetric = safeSeries.deaths.some((value) => (value ?? 0) > 0);
  const availableMetrics = (['cases', 'deaths', 'incidence_rates'] as Array<'cases' | 'deaths' | 'incidence_rates'>)
    .filter((candidate) => candidate !== 'deaths' || hasDeathsMetric);

  useEffect(() => {
    if (!availableMetrics.includes(metric)) {
      setMetric('cases');
    }
  }, [availableMetrics, metric]);

  const option = useMemo(() => ({
    animationDuration: 240,
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line' as const, lineStyle: { color: cc.line, type: 'dashed' as const } },
      backgroundColor: cc.hoverBg,
      borderColor: cc.hoverBorder,
      borderWidth: 1,
      textStyle: { color: cc.hoverFont, fontSize: 12 },
      formatter: (params: any[]) => {
        const row = params[0];
        return [
          `<b>${row?.axisValueLabel ?? row?.axisValue ?? ''}</b>`,
          `${metricConfig[lang]}: <b>${formatValue(row?.value?.[1], metric === 'incidence_rates' ? 2 : 0)}</b>`,
        ].join('<br/>');
      },
    },
    grid: { left: 60, right: 18, top: 16, bottom: 54 },
    xAxis: {
      type: 'time' as const,
      axisLabel: { color: cc.font, fontSize: 11 },
      axisLine: { lineStyle: { color: cc.line } },
      axisTick: { lineStyle: { color: cc.line } },
      splitLine: { lineStyle: { color: cc.grid, type: 'dashed' as const } },
    },
    yAxis: {
      type: 'value' as const,
      name: metricConfig[lang],
      nameTextStyle: { color: cc.font, fontSize: 11, padding: [0, 0, 8, 0] },
      axisLabel: { color: cc.font, fontSize: 11 },
      axisLine: { lineStyle: { color: cc.line } },
      axisTick: { lineStyle: { color: cc.line } },
      splitLine: { lineStyle: { color: cc.grid, type: 'dashed' as const } },
      min: 0,
    },
    dataZoom: [
      { type: 'inside' as const, filterMode: 'none' as const },
      {
        type: 'slider' as const,
        bottom: 8,
        height: 16,
        backgroundColor: cc.sliderBg,
        borderColor: cc.line,
        textStyle: { color: cc.font, fontSize: 10 },
        fillerColor: metricConfig.fill,
      },
    ],
    series: [{
        type: 'line' as const,
        name: metricConfig[lang],
        smooth: false,
        showSymbol: false,
        lineStyle: {
          color: metricConfig.color,
          width: 2.4,
          type: 'solid' as const,
        },
      areaStyle: {
        color: {
          type: 'linear',
          x: 0,
          y: 0,
          x2: 0,
          y2: 1,
          colorStops: [
            { offset: 0, color: metricConfig.fill },
            { offset: 1, color: 'rgba(255,255,255,0)' },
          ],
        },
      },
      itemStyle: { color: metricConfig.color },
      data: safeSeries.dates.map((date, index) => [date, currentValues[index] ?? null]),
    }],
  }), [cc, currentValues, lang, metric, metricConfig, safeSeries.dates]);

  if (!series || series.dates.length === 0) return null;

  const toolbar = (
    <>
      <div className="chart-toolbar">
        {availableMetrics.map((candidate) => (
          <button
            key={candidate}
            type="button"
            onClick={() => setMetric(candidate)}
            className={`chart-toggle ${metric === candidate ? 'chart-toggle-active' : ''}`}
          >
            {TREND_METRICS[candidate][lang]}
          </button>
        ))}
      </div>
      <span className="chart-chip">{startDate} → {endDate}</span>
      <span className="chart-chip">
        {lang === 'zh' ? '最新值' : 'Latest'}: {formatValue(latestValue, metric === 'incidence_rates' ? 2 : 0)}
      </span>
    </>
  );

  const table = (
    <>
      <div className="data-preview-meta">
        {lang === 'zh'
          ? '原始时间序列预览。保留病例、死亡和发病率三列，便于核对图形中的单点取值。'
          : 'Raw time-series preview retaining cases, deaths, and incidence rate columns for direct value checks.'}
      </div>
      <table className="data-preview-table">
        <thead>
          <tr>
            <th className="is-sticky">{lang === 'zh' ? '日期' : 'Date'}</th>
            <th>{lang === 'zh' ? '病例数' : 'Cases'}</th>
            {hasDeathsMetric && <th>{lang === 'zh' ? '死亡数' : 'Deaths'}</th>}
            <th>{lang === 'zh' ? '发病率（每10万）' : 'Incidence rate (per 100k)'}</th>
          </tr>
        </thead>
        <tbody>
          {safeSeries.dates.map((date, index) => (
            <tr key={date}>
              <td className="is-sticky">{date}</td>
              <td>{formatValue(safeSeries.cases[index])}</td>
              {hasDeathsMetric && <td>{formatValue(safeSeries.deaths[index])}</td>}
              <td>{formatValue(safeSeries.incidence_rates[index], 2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );

  const note = lang === 'zh'
    ? '采用更高区分度的蓝 / 橙 / 绿序列配色，主折线统一使用实线，避免不同序列因虚线而降低辨识度。'
    : 'Uses higher-contrast blue, orange, and green styling while keeping the main series on solid lines to preserve readability.';

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
      sourceMeta={sourceMeta}
    />
  );
}

function MonthlyBarChart({
  series,
  height,
  theme,
  lang,
  sourceMeta,
}: {
  series: DiseaseReportData['series'];
  height: number;
  theme: 'light' | 'dark';
  lang: 'en' | 'zh';
  sourceMeta?: ChartSourceMeta | null;
}) {
  const [metric, setMetric] = useState<'cases' | 'deaths'>('cases');
  const cc = getChartColors(theme);
  const hasDeathsMetric = useMemo(
    () => (series?.deaths ?? []).some((value) => (value ?? 0) > 0),
    [series]
  );

  const grouped = useMemo(() => {
    if (!series) return {};
    const byYear: Record<string, { months: string[]; values: number[] }> = {};
    series.dates.forEach((date, index) => {
      const [year, month] = date.slice(0, 7).split('-');
      const monthName = MONTH_NAMES[parseInt(month, 10) - 1];
      if (!byYear[year]) byYear[year] = { months: [], values: [] };
      byYear[year].months.push(monthName);
      byYear[year].values.push(metric === 'cases' ? series.cases[index] : series.deaths[index]);
    });
    return byYear;
  }, [metric, series]);

  const years = Object.keys(grouped);
  if (!series || series.dates.length === 0 || years.length === 0) return null;

  useEffect(() => {
    if (!hasDeathsMetric && metric === 'deaths') {
      setMetric('cases');
    }
  }, [hasDeathsMetric, metric]);

  const metricLabel = lang === 'zh'
    ? (metric === 'cases' ? '病例数' : '死亡数')
    : (metric === 'cases' ? 'Cases' : 'Deaths');
  const peakValue = Math.max(0, ...Object.values(grouped).flatMap(({ values }) => values));

  const option = useMemo(() => ({
    animationDuration: 240,
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' as const },
      backgroundColor: cc.hoverBg,
      borderColor: cc.hoverBorder,
      borderWidth: 1,
      textStyle: { color: cc.hoverFont, fontSize: 12 },
      formatter: (params: any[]) =>
        params
          .map((param) => `<b>${param.seriesName}</b> · ${param.name}<br/>${metricLabel}: <b>${formatValue(param.value)}</b>`)
          .join('<br/>'),
    },
    grid: { left: 60, right: 18, top: 16, bottom: 48 },
    xAxis: {
      type: 'category' as const,
      data: MONTH_NAMES,
      axisLabel: { color: cc.font, fontSize: 11 },
      axisLine: { lineStyle: { color: cc.line } },
      axisTick: { lineStyle: { color: cc.line } },
    },
    yAxis: {
      type: 'value' as const,
      name: metricLabel,
      nameTextStyle: { color: cc.font, fontSize: 11, padding: [0, 0, 8, 0] },
      axisLabel: { color: cc.font, fontSize: 11 },
      axisLine: { lineStyle: { color: cc.line } },
      axisTick: { lineStyle: { color: cc.line } },
      splitLine: { lineStyle: { color: cc.grid, type: 'dashed' as const } },
      min: 0,
    },
    series: years.map((year, index) => ({
      name: year,
      type: 'bar' as const,
      barMaxWidth: 18,
      itemStyle: {
        color: YEAR_COLORS[index % YEAR_COLORS.length],
        borderRadius: [0, 0, 0, 0],
      },
      data: MONTH_NAMES.map((monthName) => {
        const monthIndex = grouped[year].months.indexOf(monthName);
        return monthIndex >= 0 ? grouped[year].values[monthIndex] : 0;
      }),
    })),
  }), [cc, grouped, metricLabel, years]);

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

  const legend = (
    <div className="chart-legend">
      {years.map((year, index) => {
        const values = MONTH_NAMES.map((monthName) => {
          const monthIndex = grouped[year].months.indexOf(monthName);
          return monthIndex >= 0 ? grouped[year].values[monthIndex] : 0;
        });
        const yearlyTotal = values.reduce((sum, value) => sum + value, 0);
        const peakMonth = MONTH_NAMES[values.indexOf(Math.max(...values))];
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
                  {lang === 'zh' ? '峰值月' : 'Peak month'} {peakMonth}
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
                return <td key={`${year}-${monthName}`}>{formatValue(value)}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );

  const note = lang === 'zh'
    ? '图例已移至绘图区外，避免与柱体重叠；年份颜色采用高对比方案，便于追踪各年度季节性差异。'
    : 'The legend sits outside the plotting area to avoid overlap, and year colors use a higher-contrast palette for clearer seasonal comparison.';

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
      sourceMeta={sourceMeta}
    />
  );
}

function DiseaseCard({
  disease,
  defaultOpen,
  theme,
  lang,
  countryCode,
  reportId,
  sourceMeta,
}: {
  disease: DiseaseReportData;
  defaultOpen: boolean;
  theme: 'light' | 'dark';
  lang: 'en' | 'zh';
  countryCode: string;
  reportId: number | string;
  sourceMeta?: ChartSourceMeta | null;
}) {
  const [open, setOpen] = useState(defaultOpen);

  useEffect(() => {
    if (defaultOpen) setOpen(true);
  }, [defaultOpen]);

  const badgeClass = CATEGORY_COLORS[disease.category] ?? 'bg-slate-500/15 text-slate-400 ring-1 ring-slate-500/25';
  const detailHref = disease.slug ? `/countries/${countryCode}/reports/${reportId}/${disease.slug}/` : null;
  const hasDeathsData = (disease.series?.deaths ?? []).some((value) => (value ?? 0) > 0) || disease.deaths > 0;

  return (
    <div
      id={`disease-${disease.disease_id}`}
      className="border transition-all"
      style={{
        borderColor: theme === 'light' ? '#d2c7ba' : '#2a3f59',
        background: theme === 'light' ? 'rgba(255,252,248,0.88)' : 'rgba(18,29,45,0.72)',
        scrollMarginTop: '80px',
      }}
    >
      <div className="flex w-full flex-wrap items-start justify-between gap-4 p-5">
        <div className="min-w-0 flex-1">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <span className={`inline-flex flex-shrink-0 items-center px-2.5 py-1 text-xs font-medium ${badgeClass}`}>
              {lang === 'zh'
                ? (disease.category === 'Viral'
                  ? '病毒性'
                  : disease.category === 'Bacterial'
                    ? '细菌性'
                    : disease.category)
                : disease.category}
            </span>
            <span className="font-mono text-xs text-slate-600">{disease.disease_id}</span>
          </div>

          <div className="min-w-0">
            {detailHref ? (
              <a
                href={detailHref}
                className="group inline-flex items-center gap-1.5 font-semibold text-base text-slate-100 transition-colors hover:text-brand-400"
              >
                {lang === 'zh' && disease.name_zh ? disease.name_zh : disease.name_en}
                <svg className="h-3.5 w-3.5 flex-shrink-0 text-slate-600 opacity-0 transition-all group-hover:opacity-100 group-hover:text-brand-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                </svg>
              </a>
            ) : (
              <span className="font-semibold text-base text-slate-100">
                {lang === 'zh' && disease.name_zh ? disease.name_zh : disease.name_en}
              </span>
            )}
            {disease.name_zh && disease.name_zh !== disease.name_en && (
              <span className="ml-2 text-sm text-slate-500">
                {lang === 'zh' ? disease.name_en : disease.name_zh}
              </span>
            )}
          </div>
        </div>

        <div className="flex items-start gap-4">
          <div className={`hidden text-right text-sm sm:grid ${hasDeathsData ? 'grid-cols-3 gap-5' : 'grid-cols-2 gap-5'}`}>
            <div>
              <div className="font-mono text-slate-100">{disease.cases.toLocaleString()}</div>
              <div className="text-xs text-slate-600">{lang === 'zh' ? '病例' : 'Cases'}</div>
            </div>
            {hasDeathsData && (
              <div>
                <div className="font-mono text-slate-100">{disease.deaths.toLocaleString()}</div>
                <div className="text-xs text-slate-600">{lang === 'zh' ? '死亡' : 'Deaths'}</div>
              </div>
            )}
            <div>
              <div><Delta current={disease.cases} prev={disease.cases_prev_year} /></div>
              <div className="text-xs text-slate-600">{lang === 'zh' ? '同比' : 'YoY'}</div>
            </div>
          </div>

          <button
            type="button"
            onClick={() => setOpen((current) => !current)}
            title={lang === 'zh' ? '展开附图与数据' : 'Toggle appendix charts and data'}
            className="inline-flex h-9 w-9 items-center justify-center border border-slate-700/60 text-slate-500 transition-colors hover:bg-slate-800/30 hover:text-slate-300"
          >
            <svg
              className={`h-4 w-4 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>
        </div>
      </div>

      {open && (
        <div className="space-y-6 border-t border-slate-800/50 px-5 pb-6 pt-5">
          {disease.series && disease.series.dates.length > 0 ? (
            <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
              <div className="space-y-3">
                <h4 className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">
                  {lang === 'zh' ? '流行趋势（报告期）' : 'Epidemic trend (report period)'}
                </h4>
                <TrendChart series={disease.series} height={260} theme={theme} lang={lang} sourceMeta={sourceMeta} />
              </div>
              <div className="space-y-3">
                <h4 className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">
                  {lang === 'zh' ? '月度分布' : 'Monthly distribution'}
                </h4>
                <MonthlyBarChart series={disease.series} height={260} theme={theme} lang={lang} sourceMeta={sourceMeta} />
              </div>
            </div>
          ) : (
            <div className="border border-slate-700/60 bg-slate-800/10 px-4 py-3 text-sm text-slate-500">
              {lang === 'zh' ? '当前报告周期缺少可绘制的时间序列。' : 'No plot-ready time series is available for this report window.'}
            </div>
          )}

          <AnalysisSummary sections={disease.sections} lang={lang} />
        </div>
      )}
    </div>
  );
}

export default function DiseaseReportPanel({ diseases, countryCode, reportId, sourceMeta = null }: Props) {
  const theme = useTheme();
  const lang = useLang();
  const [search, setSearch] = useState('');
  const [viewMode, setViewMode] = useState<'priority' | 'all'>('priority');
  const [openId, setOpenId] = useState<string | null>(null);

  useEffect(() => {
    const hash = window.location.hash;
    if (hash.startsWith('#disease-')) {
      const id = hash.replace('#disease-', '');
      setOpenId(id);
      setTimeout(() => {
        document.getElementById(`disease-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }, 100);
    }
  }, []);

  const filtered = useMemo(() => {
    if (!search.trim()) return diseases;
    const query = search.toLowerCase();
    return diseases.filter((disease) =>
      disease.name_en.toLowerCase().includes(query)
      || disease.name_zh?.includes(query)
      || disease.disease_id.toLowerCase().includes(query)
      || disease.category.toLowerCase().includes(query)
    );
  }, [diseases, search]);

  const priorityDiseases = filtered.filter(hasStructuredCard);
  const visibleDiseases = !search.trim() && viewMode === 'priority' && priorityDiseases.length > 0
    ? priorityDiseases
    : filtered;
  const withCases = visibleDiseases.filter((disease) => disease.cases > 0);
  const zeroCases = visibleDiseases.filter((disease) => disease.cases === 0);

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1 max-w-72">
          <svg className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            type="text"
            className="site-control-input w-full border rounded-none pl-9 pr-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-brand-500"
            placeholder={lang === 'zh' ? '搜索疾病…' : 'Search disease…'}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <div className="chart-toolbar">
          <button
            type="button"
            onClick={() => setViewMode('priority')}
            className={`chart-toggle ${viewMode === 'priority' ? 'chart-toggle-active' : ''}`}
          >
            {lang === 'zh' ? '重点疾病' : 'Priority'}
          </button>
          <button
            type="button"
            onClick={() => setViewMode('all')}
            className={`chart-toggle ${viewMode === 'all' ? 'chart-toggle-active' : ''}`}
          >
            {lang === 'zh' ? '完整列表' : 'All'}
          </button>
        </div>
        <span className="chart-chip">
          {withCases.length} {lang === 'zh' ? '个有病例的疾病' : 'diseases with cases'}
        </span>
        <span className="chart-chip">
          {priorityDiseases.length} {lang === 'zh' ? '张结构化疾病卡片' : 'structured disease cards'}
        </span>
      </div>

      <div className="space-y-2">
        {withCases.map((disease, index) => (
          <DiseaseCard
            key={disease.disease_id}
            disease={disease}
            defaultOpen={
              openId === disease.disease_id
              || (!openId && !search.trim() && viewMode === 'priority' && index === 0)
            }
            theme={theme}
            lang={lang}
            countryCode={countryCode}
            reportId={reportId}
            sourceMeta={sourceMeta}
          />
        ))}
      </div>

      {zeroCases.length > 0 && !search.trim() && (
        <details className="group">
          <summary className="list-none cursor-pointer py-2 text-xs text-slate-600 transition-colors hover:text-slate-400">
            <span className="inline-flex items-center gap-2">
              <svg className="h-3.5 w-3.5 transition-transform group-open:rotate-90" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
              {lang === 'zh' ? `${zeroCases.length} 个零病例疾病` : `${zeroCases.length} diseases with no cases this period`}
            </span>
          </summary>
          <div className="mt-2 space-y-2">
            {zeroCases.map((disease) => (
              <DiseaseCard
                key={disease.disease_id}
                disease={disease}
                defaultOpen={openId === disease.disease_id}
                theme={theme}
                lang={lang}
                countryCode={countryCode}
                reportId={reportId}
                sourceMeta={sourceMeta}
              />
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
