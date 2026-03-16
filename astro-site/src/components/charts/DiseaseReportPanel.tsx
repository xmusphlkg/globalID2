// src/components/charts/DiseaseReportPanel.tsx
// Per-disease analysis panel for the report page.
// The inline expansion intentionally shows charts only.

import React, { useState, useEffect, useMemo } from 'react';
import EChartsReact from 'echarts-for-react/lib/core';
import echarts from '../../lib/echarts';

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
  /** Time series for the report period */
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
}

const CATEGORY_COLORS: Record<string, string> = {
  Viral:     'bg-blue-500/15 text-blue-400 ring-1 ring-blue-500/25',
  Bacterial: 'bg-amber-500/15 text-amber-400 ring-1 ring-amber-500/25',
  Parasitic: 'bg-green-500/15 text-green-400 ring-1 ring-green-500/25',
  Fungal:    'bg-purple-500/15 text-purple-400 ring-1 ring-purple-500/25',
};

const PALETTE = [
  '#60a5fa', '#34d399', '#f472b6', '#fb923c', '#a78bfa',
  '#38bdf8', '#4ade80', '#fbbf24', '#e879f9', '#f87171',
];
const LIGHT_PALETTE = [
  '#2563eb', '#0d9488', '#db2777', '#ea580c', '#7c3aed',
  '#0284c7', '#16a34a', '#ca8a04', '#be185d', '#dc2626',
];

const MONTH_NAMES = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

// ────────────────────────────────── helpers ──────────────────────────────────

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

function chartColors(theme: 'light' | 'dark') {
  return theme === 'light' ? {
    font: '#475569', grid: '#e2e8f0', line: '#cbd5e1', tick: '#94a3b8',
    bg: '#f1f5f9', bgSlider: '#f8fafc', legendBg: 'rgba(255,255,255,0.9)',
    legendBorder: '#cbd5e1', hoverBg: '#fff', hoverBorder: '#cbd5e1',
    hoverFont: '#0f172a', palette: LIGHT_PALETTE,
  } : {
    font: '#94a3b8', grid: '#1e293b', line: '#334155', tick: '#475569',
    bg: '#0f172a', bgSlider: '#0f172a', legendBg: 'rgba(15,23,42,0.8)',
    legendBorder: '#334155', hoverBg: '#1e293b', hoverBorder: '#475569',
    hoverFont: '#e2e8f0', palette: PALETTE,
  };
}

// ─────────────────────────── sub-charts ──────────────────────────────────────

function TrendChart({ series, height, theme, lang }: {
  series: DiseaseReportData['series'];
  height: number;
  theme: 'light' | 'dark';
  lang: 'en' | 'zh';
}) {
  const [metric, setMetric] = useState<'cases' | 'deaths'>('cases');
  const cc = chartColors(theme);

  const option = useMemo(() => {
    if (!series) return {};
    const yData = metric === 'cases' ? series.cases : series.deaths;
    const label = metric === 'cases' ? (lang === 'zh' ? '病例数' : 'Cases') : (lang === 'zh' ? '死亡数' : 'Deaths');
    return {
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis',
        backgroundColor: cc.hoverBg, borderColor: cc.hoverBorder,
        textStyle: { color: cc.hoverFont, fontSize: 12 },
      },
      grid: { left: 50, right: 16, top: 16, bottom: 52 },
      xAxis: {
        type: 'time' as const,
        axisLabel: { color: cc.font, fontSize: 10 },
        axisLine: { lineStyle: { color: cc.line } },
        axisTick: { lineStyle: { color: cc.tick } },
        splitLine: { lineStyle: { color: cc.grid } },
      },
      yAxis: {
        type: 'value' as const,
        name: label,
        nameTextStyle: { color: cc.font, fontSize: 10 },
        axisLabel: { color: cc.font, fontSize: 10 },
        axisLine: { lineStyle: { color: cc.line } },
        splitLine: { lineStyle: { color: cc.grid } },
        min: 0,
      },
      dataZoom: [{
        type: 'slider' as const, bottom: 4, height: 16,
        backgroundColor: cc.bgSlider, borderColor: cc.line,
        textStyle: { color: cc.font, fontSize: 9 },
        fillerColor: theme === 'light' ? 'rgba(37,99,235,0.15)' : 'rgba(96,165,250,0.15)',
      }],
      series: [{
        type: 'line' as const,
        name: label,
        smooth: false,
        showSymbol: false,
        lineStyle: { color: '#0d9488', width: 2 },
        areaStyle: {
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(13,148,136,0.25)' },
              { offset: 1, color: 'rgba(13,148,136,0.02)' },
            ],
          },
        },
        itemStyle: { color: '#0d9488' },
        data: series.dates.map((d, i) => [d, yData[i] ?? null]),
      }],
    };
  }, [series, metric, theme, lang, cc]);

  if (!series || series.dates.length === 0) return null;

  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        {(['cases', 'deaths'] as const).map(m => (
          <button key={m} onClick={() => setMetric(m)}
            className={`px-2.5 py-0.5 text-xs font-medium rounded-full border transition-all ${
              metric === m
                ? 'bg-teal-500/20 border-teal-500/40 text-teal-400'
                : 'border-slate-700 text-slate-500 hover:border-slate-600 hover:text-slate-400'
            }`}
          >
            {m === 'cases' ? (lang === 'zh' ? '病例' : 'Cases') : (lang === 'zh' ? '死亡' : 'Deaths')}
          </button>
        ))}
      </div>
      <EChartsReact echarts={echarts} option={option} notMerge style={{ width: '100%', height }} />
    </div>
  );
}

function MonthlyBarChart({ series, height, theme, lang }: {
  series: DiseaseReportData['series'];
  height: number;
  theme: 'light' | 'dark';
  lang: 'en' | 'zh';
}) {
  const [metric, setMetric] = useState<'cases' | 'deaths'>('cases');
  const cc = chartColors(theme);

  const grouped = useMemo(() => {
    if (!series) return {};
    const byYear: Record<string, { months: string[]; values: number[] }> = {};
    series.dates.forEach((d, i) => {
      const ym = d.substring(0, 7);
      const [year, mon] = ym.split('-');
      const monthName = MONTH_NAMES[parseInt(mon, 10) - 1];
      if (!byYear[year]) byYear[year] = { months: [], values: [] };
      byYear[year].months.push(monthName);
      byYear[year].values.push(metric === 'cases' ? series.cases[i] : series.deaths[i]);
    });
    return byYear;
  }, [series, metric]);

  const option = useMemo(() => {
    const label = metric === 'cases' ? (lang === 'zh' ? '病例数' : 'Cases') : (lang === 'zh' ? '死亡数' : 'Deaths');
    return {
      backgroundColor: 'transparent',
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'shadow' },
        backgroundColor: cc.hoverBg, borderColor: cc.hoverBorder,
        textStyle: { color: cc.hoverFont, fontSize: 12 },
      },
      legend: {
        bottom: 0, left: 0, orient: 'horizontal' as const,
        textStyle: { color: cc.font, fontSize: 10 },
        backgroundColor: cc.legendBg, borderColor: cc.legendBorder, borderWidth: 1,
      },
      grid: { left: 50, right: 16, top: 16, bottom: 60 },
      xAxis: {
        type: 'category' as const, data: MONTH_NAMES,
        axisLabel: { color: cc.font, fontSize: 10 },
        axisLine: { lineStyle: { color: cc.line } },
        axisTick: { lineStyle: { color: cc.tick } },
        splitLine: { lineStyle: { color: cc.grid } },
      },
      yAxis: {
        type: 'value' as const, name: label,
        nameTextStyle: { color: cc.font, fontSize: 10 },
        axisLabel: { color: cc.font, fontSize: 10 },
        axisLine: { lineStyle: { color: cc.line } },
        splitLine: { lineStyle: { color: cc.grid } }, min: 0,
      },
      series: Object.entries(grouped).map(([year, { months, values }], idx) => ({
        name: year, type: 'bar' as const,
        data: MONTH_NAMES.map(mon => {
          const i = months.indexOf(mon);
          return i >= 0 ? values[i] : 0;
        }),
        itemStyle: { color: cc.palette[idx % cc.palette.length], opacity: 0.85 },
      })),
    };
  }, [grouped, metric, theme, lang, cc]);

  if (!series || series.dates.length === 0 || Object.keys(grouped).length === 0) return null;

  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        {(['cases', 'deaths'] as const).map(m => (
          <button key={m} onClick={() => setMetric(m)}
            className={`px-2.5 py-0.5 text-xs font-medium rounded-full border transition-all ${
              metric === m
                ? 'bg-blue-500/20 border-blue-500/40 text-blue-400'
                : 'border-slate-700 text-slate-500 hover:border-slate-600 hover:text-slate-400'
            }`}
          >
            {m === 'cases' ? (lang === 'zh' ? '病例' : 'Cases') : (lang === 'zh' ? '死亡' : 'Deaths')}
          </button>
        ))}
      </div>
      <EChartsReact echarts={echarts} option={option} notMerge style={{ width: '100%', height }} />
    </div>
  );
}

// ─────────────────────────── disease card ────────────────────────────────────

function DiseaseCard({ disease, defaultOpen, theme, lang, countryCode, reportId }: {
  disease: DiseaseReportData;
  defaultOpen: boolean;
  theme: 'light' | 'dark';
  lang: 'en' | 'zh';
  countryCode: string;
  reportId: number | string;
}) {
  const [open, setOpen] = useState(defaultOpen);

  const badgeClass = CATEGORY_COLORS[disease.category] ?? 'bg-slate-500/15 text-slate-400 ring-1 ring-slate-500/25';
  const detailHref = disease.slug ? `/countries/${countryCode}/reports/${reportId}/${disease.slug}/` : null;

  function Delta({ current, prev }: { current: number; prev: number }) {
    if (prev === 0 && current === 0) return <span className="text-slate-600">—</span>;
    const diff = current - prev;
    const pct = prev > 0 ? ((Math.abs(diff) / prev) * 100).toFixed(0) : '∞';
    if (diff === 0) return <span className="text-slate-500 text-xs">=</span>;
    return diff > 0
      ? <span className="text-red-400 text-xs">▲{pct}%</span>
      : <span className="text-emerald-400 text-xs">▼{pct}%</span>;
  }

  return (
    <div
      id={`disease-${disease.disease_id}`}
      className="rounded-2xl border transition-all"
      style={{
        borderColor: theme === 'light' ? '#e2e8f0' : '#1e293b',
        background: theme === 'light' ? '#ffffff' : '#0f172a',
        scrollMarginTop: '80px',
      }}
    >
      {/* Header — always visible */}
      <div className="w-full flex items-center justify-between gap-4 p-5">
        {/* Disease name: links to detail page */}
        <div className="flex items-center gap-3 min-w-0 flex-1">
          <span className={`flex-shrink-0 text-xs font-medium px-2.5 py-1 rounded-full ${badgeClass}`}>
            {lang === 'zh' ? (disease.category === 'Viral' ? '病毒性' : disease.category === 'Bacterial' ? '细菌性' : disease.category) : disease.category}
          </span>
          <div className="min-w-0">
            {detailHref ? (
              <a
                href={detailHref}
                className="font-semibold text-base text-slate-100 hover:text-teal-400 transition-colors inline-flex items-center gap-1.5 group"
              >
                {lang === 'zh' && disease.name_zh ? disease.name_zh : disease.name_en}
                <svg className="w-3.5 h-3.5 text-slate-600 group-hover:text-teal-400 flex-shrink-0 opacity-0 group-hover:opacity-100 transition-all" fill="none" viewBox="0 0 24 24" stroke="currentColor">
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

        <div className="flex items-center gap-3 flex-shrink-0">
          {/* Quick stats */}
          <div className="hidden sm:flex items-center gap-5 text-sm">
            <div className="text-right">
              <div className="font-mono text-slate-100">{disease.cases.toLocaleString()}</div>
              <div className="text-xs text-slate-600">{lang === 'zh' ? '病例' : 'Cases'}</div>
            </div>
            <div className="text-right">
              <div className="flex items-center gap-1.5 justify-end">
                <span className="font-mono text-slate-100">{disease.deaths.toLocaleString()}</span>
              </div>
              <div className="text-xs text-slate-600">{lang === 'zh' ? '死亡' : 'Deaths'}</div>
            </div>
            <div className="text-right">
              <Delta current={disease.cases} prev={disease.cases_prev_year} />
              <div className="text-xs text-slate-600">{lang === 'zh' ? '同比' : 'YoY'}</div>
            </div>
          </div>

          {/* Expand/collapse inline preview */}
          <button
            onClick={() => setOpen(o => !o)}
            title={lang === 'zh' ? '展开预览' : 'Toggle inline preview'}
            className="p-1.5 rounded-lg hover:bg-slate-800 transition-colors"
          >
            <svg
              className={`w-4 h-4 text-slate-500 transition-transform duration-200 ${open ? 'rotate-180' : ''}`}
              fill="none" viewBox="0 0 24 24" stroke="currentColor"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>
        </div>
      </div>

      {/* Expanded content */}
      {open && (
        <div className="px-5 pb-6 pt-1 border-t border-slate-800/50 space-y-6">
          {/* Charts grid */}
          {disease.series && disease.series.dates.length > 0 && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div>
                <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-3">
                  {lang === 'zh' ? '流行趋势（报告期）' : 'Epidemic Trend (Report Period)'}
                </h4>
                <TrendChart series={disease.series} height={220} theme={theme} lang={lang} />
              </div>
              <div>
                <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-widest mb-3">
                  {lang === 'zh' ? '月度分布' : 'Monthly Distribution'}
                </h4>
                <MonthlyBarChart series={disease.series} height={220} theme={theme} lang={lang} />
              </div>
            </div>
          )}

        </div>
      )}
    </div>
  );
}

// ──────────────────────────── main export ────────────────────────────────────

export default function DiseaseReportPanel({ diseases, countryCode, reportId }: Props) {
  const theme = useTheme();
  const lang = useLang();
  const [search, setSearch] = useState('');
  const [openId, setOpenId] = useState<string | null>(null);

  // Auto-open disease from URL hash on mount
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
    const q = search.toLowerCase();
    return diseases.filter(
      d => d.name_en.toLowerCase().includes(q) ||
           d.name_zh?.includes(q) ||
           d.disease_id.toLowerCase().includes(q) ||
           d.category.toLowerCase().includes(q)
    );
  }, [diseases, search]);

  const withCases = filtered.filter(d => d.cases > 0);
  const zeroCases = filtered.filter(d => d.cases === 0);

  return (
    <div className="space-y-4">
      {/* Search */}
      <div className="flex items-center gap-4">
        <div className="relative flex-1 max-w-64">
          <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
          <input
            type="text"
            className="site-control-input w-full border text-sm rounded-lg pl-9 pr-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-brand-500"
            placeholder={lang === 'zh' ? '搜索疾病…' : 'Search disease…'}
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <span className="text-xs text-slate-600">
          {withCases.length} {lang === 'zh' ? '个有病例的疾病' : 'diseases with cases'}
        </span>
      </div>

      {/* Cards with cases */}
      <div className="space-y-2">
        {withCases.map(d => (
          <DiseaseCard
            key={d.disease_id}
            disease={d}
            defaultOpen={openId === d.disease_id}
            theme={theme}
            lang={lang}
            countryCode={countryCode}
            reportId={reportId}
          />
        ))}
      </div>

      {/* Zero-case diseases (collapsed section) */}
      {zeroCases.length > 0 && !search.trim() && (
        <details className="group">
          <summary className="cursor-pointer text-xs text-slate-600 hover:text-slate-400 transition-colors list-none flex items-center gap-2 py-2">
            <svg className="w-3.5 h-3.5 group-open:rotate-90 transition-transform" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
            </svg>
            {lang === 'zh' ? `${zeroCases.length} 个零病例疾病` : `${zeroCases.length} diseases with no cases this period`}
          </summary>
          <div className="mt-2 space-y-2">
            {zeroCases.map(d => (
              <DiseaseCard
                key={d.disease_id}
                disease={d}
                defaultOpen={openId === d.disease_id}
                theme={theme}
                lang={lang}
                countryCode={countryCode}
                reportId={reportId}
              />
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
