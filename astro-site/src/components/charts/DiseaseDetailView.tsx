// src/components/charts/DiseaseDetailView.tsx
// Journal-publication-style disease detail view for individual disease pages.
// Sections: Summary → Highlights → Key Findings → Trend Analysis
// Charts: Epidemic Curve (cases+deaths dual axis) + Monthly Distribution (cases & deaths)

import React, { useState, useEffect, useMemo } from 'react';
import EChartsReact from 'echarts-for-react/lib/core';
import echarts from '../../lib/echarts';

// ─── Types ────────────────────────────────────────────────────────────────────

interface DiseaseSection {
  section_type: string;
  title: string;
  content: string;
  content_html?: string;
}

interface DiseaseMeta {
  disease_id: string;
  name_en: string;
  name_zh: string;
  category: string;
  slug: string;
}

interface DiseaseSeries {
  dates: string[];
  cases: number[];
  deaths: number[];
  incidence_rates: (number | null)[];
  total_cases: number;
  total_deaths: number;
}

interface ReportMeta {
  id: number;
  title: string;
  period_start: string;
  period_end: string;
  country_name: string;
  country_code: string;
}

interface Props {
  diseaseMeta: DiseaseMeta;
  sections: DiseaseSection[];
  series: DiseaseSeries | null;
  reportMeta: ReportMeta;
}

// ─── Theme ───────────────────────────────────────────────────────────────────

function useTheme() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    if (typeof document === 'undefined') return 'dark';
    return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  });
  useEffect(() => {
    if (typeof document === 'undefined') return;
    const root = document.documentElement;
    const update = () => setTheme(root.getAttribute('data-theme') === 'light' ? 'light' : 'dark');
    update();
    const obs = new MutationObserver(update);
    obs.observe(root, { attributes: true, attributeFilter: ['data-theme'] });
    return () => obs.disconnect();
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

// ─── Chart color tokens ───────────────────────────────────────────────────────

function chartTokens(theme: 'light' | 'dark') {
  return theme === 'light' ? {
    font: '#475569',
    grid: '#e2e8f0',
    line: '#cbd5e1',
    tick: '#94a3b8',
    bg: 'transparent',
    sliderBg: '#f8fafc',
    legendBg: 'rgba(255,255,255,0.92)',
    legendBorder: '#cbd5e1',
    tooltipBg: '#ffffff',
    tooltipBorder: '#e2e8f0',
    tooltipFont: '#0f172a',
    casesColor: '#2563eb',    // blue-600
    casesAreaA: 'rgba(37,99,235,0.18)',
    casesAreaB: 'rgba(37,99,235,0.02)',
    deathsColor: '#dc2626',   // red-600
    barColor: '#3b82f6',
    barColorAlt: '#0d9488',
  } : {
    font: '#94a3b8',
    grid: '#1e293b',
    line: '#334155',
    tick: '#475569',
    bg: 'transparent',
    sliderBg: '#0f172a',
    legendBg: 'rgba(15,23,42,0.85)',
    legendBorder: '#334155',
    tooltipBg: '#1e293b',
    tooltipBorder: '#334155',
    tooltipFont: '#e2e8f0',
    casesColor: '#60a5fa',    // blue-400
    casesAreaA: 'rgba(96,165,250,0.18)',
    casesAreaB: 'rgba(96,165,250,0.02)',
    deathsColor: '#f87171',   // red-400
    barColor: '#3b82f6',
    barColorAlt: '#2dd4bf',
  };
}

// ─── Number formatter ─────────────────────────────────────────────────────────

function fmtNum(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(0) + 'K';
  return n.toLocaleString();
}

function fmtDate(d: string) {
  return new Date(d).toLocaleDateString('en-US', { year: 'numeric', month: 'short' });
}

const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const YEAR_PALETTE_DARK = [
  '#60a5fa', '#34d399', '#f472b6', '#fb923c', '#a78bfa',
  '#38bdf8', '#4ade80', '#fbbf24', '#e879f9', '#f87171',
  '#22d3ee', '#86efac',
];
const YEAR_PALETTE_LIGHT = [
  '#2563eb', '#0d9488', '#db2777', '#ea580c', '#7c3aed',
  '#0284c7', '#16a34a', '#ca8a04', '#be185d', '#dc2626',
  '#0891b2', '#65a30d',
];

// ─── Epidemic Curve (dual-axis: cases bars + deaths line) ────────────────────

function EpidemicCurveChart({ series, theme, lang }: {
  series: DiseaseSeries;
  theme: 'light' | 'dark';
  lang: 'en' | 'zh';
}) {
  const t = chartTokens(theme);

  const option = useMemo(() => {
    const casesLabel = lang === 'zh' ? '病例数' : 'Cases';
    const deathsLabel = lang === 'zh' ? '死亡数' : 'Deaths';

    return {
      backgroundColor: t.bg,
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross', crossStyle: { color: t.line } },
        backgroundColor: t.tooltipBg,
        borderColor: t.tooltipBorder,
        borderWidth: 1,
        textStyle: { color: t.tooltipFont, fontSize: 12 },
        formatter: (params: any[]) => {
          const d = params[0]?.axisValue;
          const dateStr = d ? new Date(d).toLocaleDateString('en-US', { year: 'numeric', month: 'short' }) : '';
          let html = `<div style="font-size:11px;color:${t.font};margin-bottom:4px">${dateStr}</div>`;
          params.forEach((p: any) => {
            const v = p.value?.[1] ?? p.value ?? 0;
            html += `<div style="display:flex;align-items:center;gap:6px;margin-top:2px">
              <span style="width:8px;height:8px;border-radius:50%;background:${p.color};display:inline-block"></span>
              <span style="color:${t.font}">${p.seriesName}:</span>
              <span style="color:${t.tooltipFont};font-weight:600">${v.toLocaleString()}</span>
            </div>`;
          });
          return html;
        },
      },
      legend: {
        top: 4, right: 8,
        textStyle: { color: t.font, fontSize: 11 },
        itemWidth: 14, itemHeight: 8,
        backgroundColor: t.legendBg,
        borderColor: t.legendBorder,
        borderWidth: 1,
        padding: [4, 8],
        borderRadius: 4,
      },
      grid: { left: 60, right: 60, top: 44, bottom: 60 },
      xAxis: {
        type: 'time' as const,
        axisLabel: {
          color: t.font, fontSize: 10,
          formatter: (val: number) => {
            const d = new Date(val);
            return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
          },
          rotate: 30,
        },
        axisLine: { lineStyle: { color: t.line } },
        axisTick: { lineStyle: { color: t.tick } },
        splitLine: { show: false },
      },
      yAxis: [
        {
          type: 'value' as const,
          name: casesLabel,
          nameTextStyle: { color: t.casesColor, fontSize: 11, fontWeight: 600, padding: [0, 0, 0, 50] },
          axisLabel: { color: t.font, fontSize: 10, formatter: fmtNum },
          axisLine: { lineStyle: { color: t.casesColor, width: 2 } },
          splitLine: { lineStyle: { color: t.grid } },
          min: 0,
        },
        {
          type: 'value' as const,
          name: deathsLabel,
          nameTextStyle: { color: t.deathsColor, fontSize: 11, fontWeight: 600, padding: [0, 50, 0, 0] },
          axisLabel: { color: t.font, fontSize: 10, formatter: fmtNum },
          axisLine: { lineStyle: { color: t.deathsColor, width: 2 } },
          splitLine: { show: false },
          min: 0,
        },
      ],
      dataZoom: [{
        type: 'slider' as const, bottom: 4, height: 18,
        backgroundColor: t.sliderBg, borderColor: t.line,
        textStyle: { color: t.font, fontSize: 9 },
        fillerColor: theme === 'light' ? 'rgba(37,99,235,0.12)' : 'rgba(96,165,250,0.12)',
        start: Math.max(0, 100 - Math.round(60 / series.dates.length * 100)),
        end: 100,
      }],
      series: [
        {
          name: casesLabel,
          type: 'bar' as const,
          yAxisIndex: 0,
          barMaxWidth: 10,
          itemStyle: { color: t.casesColor, opacity: 0.8 },
          emphasis: { itemStyle: { opacity: 1 } },
          data: series.dates.map((d, i) => [d, series.cases[i] ?? 0]),
        },
        {
          name: deathsLabel,
          type: 'line' as const,
          yAxisIndex: 1,
          smooth: true,
          showSymbol: false,
          symbolSize: 4,
          lineStyle: { color: t.deathsColor, width: 2 },
          itemStyle: { color: t.deathsColor },
          data: series.dates.map((d, i) => [d, series.deaths[i] ?? 0]),
        },
      ],
    };
  }, [series, theme, lang, t]);

  return (
    <EChartsReact
      echarts={echarts}
      option={option}
      notMerge
      style={{ width: '100%', height: 360 }}
    />
  );
}

// ─── Monthly Distribution Chart ───────────────────────────────────────────────

function MonthlyDistributionChart({ series, metric, theme, lang }: {
  series: DiseaseSeries;
  metric: 'cases' | 'deaths';
  theme: 'light' | 'dark';
  lang: 'en' | 'zh';
}) {
  const t = chartTokens(theme);
  const palette = theme === 'light' ? YEAR_PALETTE_LIGHT : YEAR_PALETTE_DARK;
  const values = metric === 'cases' ? series.cases : series.deaths;
  const label = metric === 'cases'
    ? (lang === 'zh' ? '病例数' : 'Cases')
    : (lang === 'zh' ? '死亡数' : 'Deaths');

  const grouped = useMemo(() => {
    const byYear: Record<string, number[]> = {};
    series.dates.forEach((d, i) => {
      const [year, mon] = d.substring(0, 7).split('-');
      const monIdx = parseInt(mon, 10) - 1;
      if (!byYear[year]) byYear[year] = new Array(12).fill(0);
      byYear[year][monIdx] = values[i] ?? 0;
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
      padding: [3, 6], borderRadius: 4,
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

// ─── Section renderer ─────────────────────────────────────────────────────────

function SectionBlock({ section, theme }: {
  section: DiseaseSection;
  theme: 'light' | 'dark';
}) {
  const isLight = theme === 'light';
  const borderColor = isLight ? '#e2e8f0' : '#1e293b';
  const accentColor = {
    summary:        isLight ? '#2563eb' : '#60a5fa',
    highlights:     isLight ? '#0d9488' : '#2dd4bf',
    key_findings:   isLight ? '#7c3aed' : '#a78bfa',
    trend_analysis: isLight ? '#0891b2' : '#38bdf8',
  }[section.section_type] ?? (isLight ? '#475569' : '#64748b');

  const labels: Record<string, { en: string; zh: string; icon: string }> = {
    summary:        { en: 'Summary',        zh: '摘要',   icon: '◈' },
    highlights:     { en: 'Highlights',     zh: '要点',   icon: '◆' },
    key_findings:   { en: 'Key Findings',   zh: '关键发现', icon: '◉' },
    trend_analysis: { en: 'Trend Analysis', zh: '趋势分析', icon: '◎' },
  };
  const meta = labels[section.section_type] ?? { en: section.section_type, zh: section.section_type, icon: '○' };

  return (
    <div
      style={{
        borderLeft: `3px solid ${accentColor}`,
        background: isLight ? '#fafbfd' : 'rgba(15,23,42,0.6)',
        borderRadius: '0 12px 12px 0',
        borderStyle: 'solid',
        borderWidth: '1px',
        borderColor,
        borderLeftWidth: '3px',
        borderLeftColor: accentColor,
      }}
      className="p-6 md:p-8"
    >
      <div className="flex items-center gap-3 mb-4">
        <span style={{ color: accentColor, fontSize: '16px', fontWeight: 700 }}>{meta.icon}</span>
        <h3 style={{ color: accentColor }}
          className="text-xs font-bold uppercase tracking-[0.15em]">
          {meta.en}
        </h3>
      </div>
      {section.content_html ? (
        <div
          className="prose prose-sm max-w-none journal-markdown"
          style={{
            color: isLight ? '#334155' : '#cbd5e1',
            lineHeight: '1.8',
            fontSize: '14px',
          }}
          dangerouslySetInnerHTML={{ __html: section.content_html }}
        />
      ) : (
        <p className="text-sm leading-relaxed whitespace-pre-wrap"
          style={{ color: isLight ? '#475569' : '#94a3b8' }}>
          {section.content}
        </p>
      )}
    </div>
  );
}

// ─── Figure wrapper ───────────────────────────────────────────────────────────

function Figure({ number, caption, children, theme }: {
  number: number;
  caption: string;
  children: React.ReactNode;
  theme: 'light' | 'dark';
}) {
  const isLight = theme === 'light';
  return (
    <figure
      style={{
        background: isLight ? '#ffffff' : 'rgba(15,23,42,0.5)',
        border: `1px solid ${isLight ? '#e2e8f0' : '#1e293b'}`,
        borderRadius: '12px',
      }}
      className="p-4 md:p-6"
    >
      <div className="mb-3">
        {children}
      </div>
      <figcaption
        style={{ color: isLight ? '#64748b' : '#94a3b8', fontSize: '12px', lineHeight: '1.5', borderTop: `1px solid ${isLight ? '#f1f5f9' : '#1e293b'}` }}
        className="text-center mt-2 pt-3"
      >
        <span style={{ fontWeight: 700 }}>Figure {number}.</span> {caption}
      </figcaption>
    </figure>
  );
}

// ─── Category badge ───────────────────────────────────────────────────────────

const CATEGORY_STYLES: Record<string, { bg: string; text: string; border: string }> = {
  Viral:     { bg: 'rgba(37,99,235,0.1)',   text: '#60a5fa', border: 'rgba(37,99,235,0.25)' },
  Bacterial: { bg: 'rgba(245,158,11,0.1)',  text: '#fbbf24', border: 'rgba(245,158,11,0.25)' },
  Parasitic: { bg: 'rgba(16,185,129,0.1)',  text: '#34d399', border: 'rgba(16,185,129,0.25)' },
  Fungal:    { bg: 'rgba(167,139,250,0.1)', text: '#a78bfa', border: 'rgba(167,139,250,0.25)' },
};

// ─── Main component ───────────────────────────────────────────────────────────

export default function DiseaseDetailView({ diseaseMeta, sections, series, reportMeta }: Props) {
  const theme = useTheme();
  const lang = useLang();
  const isLight = theme === 'light';

  const bgPage   = isLight ? '#f8fafc' : '#060d1b';
  const bgCard   = isLight ? '#ffffff' : '#0c1526';
  const border   = isLight ? '#e2e8f0' : '#1a2744';
  const textHead = isLight ? '#0f172a' : '#f1f5f9';
  const textBody = isLight ? '#475569' : '#94a3b8';
  const textMute = isLight ? '#94a3b8' : '#475569';

  // Get section by type
  const getSection = (type: string) =>
    sections.find(s => s.section_type === type) ?? null;

  const summarySection    = getSection('summary');
  const highlightsSection = getSection('highlights');
  const keyFindingsSection = getSection('key_findings');
  const trendSection      = getSection('trend_analysis');

  const catStyle = CATEGORY_STYLES[diseaseMeta.category] ?? {
    bg: 'rgba(100,116,139,0.1)', text: '#94a3b8', border: 'rgba(100,116,139,0.25)'
  };

  // Compute aggregate stats
  const totalCases  = series?.total_cases ?? 0;
  const totalDeaths = series?.total_deaths ?? 0;
  const cfr = totalCases > 0 ? ((totalDeaths / totalCases) * 100) : 0;

  const hasSeries = !!series && series.dates.length > 0;

  // Fix highlights: replace bullet "• " and <br/> with proper HTML
  function renderHighlights(html: string) {
    // The highlights content may be plain "• ... <br/>" or proper markdown  
    if (!html.includes('<')) {
      // Plain text bullets
      return '<ul>' + html.split('•').filter(Boolean).map(b =>
        `<li>${b.trim().replace(/<br\s*\/?>/gi, '').trim()}</li>`
      ).join('') + '</ul>';
    }
    return html;
  }

  return (
    <div style={{ background: bgPage, minHeight: '100vh' }}>
      {/* ── Journal-style page wrapper ── */}
      <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-10 space-y-8">

        {/* ── HEADER ── */}
        <header
          style={{ background: bgCard, border: `1px solid ${border}`, borderRadius: '16px' }}
          className="p-7 md:p-10"
        >
          {/* Top row: category + back link */}
          <div className="flex items-start justify-between flex-wrap gap-4 mb-6">
            <div className="flex items-center gap-3">
              <span
                style={{ background: catStyle.bg, color: catStyle.text, border: `1px solid ${catStyle.border}` }}
                className="text-xs font-bold uppercase tracking-[0.12em] px-3 py-1 rounded-full"
              >
                {diseaseMeta.category}
              </span>
              <span style={{ color: textMute }} className="text-sm">{reportMeta.country_name}</span>
              <span style={{ color: textMute }} className="text-xs opacity-60">
                Report #{reportMeta.id}
              </span>
            </div>
            <a
              href={`/countries/${reportMeta.country_code}/reports/${reportMeta.id}/`}
              style={{ color: textMute }}
              className="text-xs hover:text-brand-400 transition-colors flex items-center gap-1.5"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
              </svg>
              {lang === 'zh' ? '返回报告' : 'Back to report'}
            </a>
          </div>

          {/* Disease title */}
          <div className="mb-6">
            <h1 style={{ color: textHead }} className="text-3xl md:text-4xl font-bold tracking-tight leading-tight mb-1">
              {lang === 'zh' && diseaseMeta.name_zh ? diseaseMeta.name_zh : diseaseMeta.name_en}
            </h1>
            {diseaseMeta.name_zh && (
              <p style={{ color: textMute }} className="text-base mt-1">
                {lang === 'zh' ? diseaseMeta.name_en : diseaseMeta.name_zh}
              </p>
            )}
          </div>

          {/* Stats row */}
          {series && (
            <div
              style={{ background: isLight ? '#f8fafc' : 'rgba(255,255,255,0.03)', borderRadius: '10px', border: `1px solid ${border}` }}
              className="grid grid-cols-2 sm:grid-cols-4 gap-0"
            >
              {[
                { label: lang === 'zh' ? '累计病例' : 'Total Cases',  value: fmtNum(totalCases),  color: isLight ? '#2563eb' : '#60a5fa' },
                { label: lang === 'zh' ? '累计死亡' : 'Total Deaths', value: fmtNum(totalDeaths), color: isLight ? '#dc2626' : '#f87171' },
                { label: lang === 'zh' ? '病死率'   : 'Case Fatality', value: cfr.toFixed(3) + '%', color: isLight ? '#d97706' : '#fbbf24' },
                {
                  label: lang === 'zh' ? '数据跨度' : 'Data Span',
                  value: series.dates[0]
                    ? `${series.dates[0].substring(0,7)} – ${series.dates[series.dates.length-1].substring(0,7)}`
                    : '—',
                  color: textBody,
                },
              ].map(({ label, value, color }, i) => (
                <div key={i} className="p-4 text-center" style={{ borderColor: border }}>
                  <div style={{ color, fontWeight: 700, fontSize: '18px', fontVariantNumeric: 'tabular-nums' }}>
                    {value}
                  </div>
                  <div style={{ color: textMute }} className="text-xs mt-1">{label}</div>
                </div>
              ))}
            </div>
          )}

          {/* Period row */}
          <div className="mt-5 flex flex-wrap gap-6">
            <div className="flex items-center gap-2" style={{ color: textBody }}>
              <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
              </svg>
              <span className="text-sm">
                <span style={{ color: textMute }}>{lang === 'zh' ? '报告周期' : 'Report period'}: </span>
                <span style={{ color: textHead }}>
                  {fmtDate(reportMeta.period_start)} → {fmtDate(reportMeta.period_end)}
                </span>
              </span>
            </div>
          </div>
        </header>

        {/* ── DIVIDER: journal-style ── */}
        <div className="flex items-center gap-4">
          <div style={{ flex: 1, height: '1px', background: `linear-gradient(to right, ${border}, transparent)` }} />
          <span style={{ color: textMute, fontSize: '10px', letterSpacing: '0.2em', fontWeight: 600 }} className="uppercase">
            {lang === 'zh' ? '疾病监测报告' : 'Surveillance Analysis'}
          </span>
          <div style={{ flex: 1, height: '1px', background: `linear-gradient(to left, ${border}, transparent)` }} />
        </div>

        {/* ── SUMMARY ── */}
        {summarySection && (
          <section>
            <SectionBlock section={summarySection} theme={theme} />
          </section>
        )}

        {/* ── HIGHLIGHTS ── */}
        {highlightsSection && (
          <section>
            <SectionBlock
              section={{
                ...highlightsSection,
                content_html: highlightsSection.content_html
                  ? renderHighlights(highlightsSection.content_html)
                  : undefined,
              }}
              theme={theme}
            />
          </section>
        )}

        {/* ── KEY FINDINGS ── */}
        {keyFindingsSection && (
          <section>
            <SectionBlock section={keyFindingsSection} theme={theme} />
          </section>
        )}

        {/* ── FIGURES ── */}
        {hasSeries && (
          <section>
            {/* Section heading */}
            <div className="flex items-center gap-3 mb-5">
              <div style={{ width: '3px', height: '20px', background: isLight ? '#0891b2' : '#38bdf8', borderRadius: '2px' }} />
              <h2 style={{ color: textHead }} className="text-sm font-bold uppercase tracking-[0.15em]">
                {lang === 'zh' ? '图表分析' : 'Figures'}
              </h2>
            </div>

            {/* Figure 1: Epidemic Curve */}
            <Figure
              number={1}
              caption={
                lang === 'zh'
                  ? `${diseaseMeta.name_zh || diseaseMeta.name_en} 流行曲线——月度病例数（柱）与死亡数（线），双纵轴`
                  : `Epidemic curve for ${diseaseMeta.name_en}. Monthly reported cases (bars, left axis) and deaths (line, right axis) over the full surveillance period.`
              }
              theme={theme}
            >
              <EpidemicCurveChart series={series!} theme={theme} lang={lang} />
            </Figure>

            {/* Figures 2 & 3: Monthly Distribution side by side */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mt-6">
              <Figure
                number={2}
                caption={lang === 'zh' ? '月度病例分布（按年分组）' : 'Monthly distribution of cases by year.'}
                theme={theme}
              >
                <MonthlyDistributionChart series={series!} metric="cases" theme={theme} lang={lang} />
              </Figure>
              <Figure
                number={3}
                caption={lang === 'zh' ? '月度死亡分布（按年分组）' : 'Monthly distribution of deaths by year.'}
                theme={theme}
              >
                <MonthlyDistributionChart series={series!} metric="deaths" theme={theme} lang={lang} />
              </Figure>
            </div>
          </section>
        )}

        {/* ── TREND ANALYSIS ── */}
        {trendSection && (
          <section>
            <SectionBlock section={trendSection} theme={theme} />
          </section>
        )}

        {/* ── FOOTER NAV ── */}
        <div
          style={{ borderTop: `1px solid ${border}`, paddingTop: '24px' }}
          className="flex items-center justify-between flex-wrap gap-4"
        >
          <a
            href={`/countries/${reportMeta.country_code}/reports/${reportMeta.id}/`}
            style={{ color: textBody }}
            className="flex items-center gap-2 text-sm hover:text-brand-400 transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
            </svg>
            {lang === 'zh' ? '← 返回报告列表' : '← Back to disease list'}
          </a>
          <a
            href={`/diseases/${diseaseMeta.slug}/`}
            style={{ color: textBody }}
            className="flex items-center gap-2 text-sm hover:text-brand-400 transition-colors"
          >
            {lang === 'zh' ? '查看全球历史数据 →' : 'View global disease page →'}
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
            </svg>
          </a>
        </div>

      </div>
    </div>
  );
}
