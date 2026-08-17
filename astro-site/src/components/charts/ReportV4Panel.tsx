import React, { useEffect, useMemo, useState } from 'react';
import { marked } from 'marked';
import { loadCountryDataset, type CountryDatasetSeriesEntry } from './countryDataset';

type Lang = 'zh' | 'en';
type AnyRecord = Record<string, any>;
type SparklineSeriesEntry = Pick<CountryDatasetSeriesEntry, 'cases' | 'weekly_equiv_cases'> & {
  dates?: string[];
  monthly_cases?: number[];
  annual_cases?: number[];
  current_year?: string | null;
  current_year_cumulative_cases?: number | null;
};

interface Props {
  report: AnyRecord;
  countryDataUrl?: string;
  sparklineSeries?: Record<string, SparklineSeriesEntry>;
}

function useLang(): Lang {
  // Report pages are rendered in Chinese first. Keep the server and hydration
  // renders identical, then sync a previously selected language in the effect.
  const [lang, setLang] = useState<Lang>('zh');

  useEffect(() => {
    if (typeof document === 'undefined') return;
    const root = document.documentElement;
    const update = () => setLang(root.getAttribute('data-lang') === 'en' ? 'en' : 'zh');
    update();
    const observer = new MutationObserver(update);
    observer.observe(root, { attributes: true, attributeFilter: ['data-lang'] });
    document.addEventListener('globalid:language-change', update);
    window.addEventListener('storage', update);
    return () => {
      observer.disconnect();
      document.removeEventListener('globalid:language-change', update);
      window.removeEventListener('storage', update);
    };
  }, []);

  return lang;
}

function asRecord(value: unknown): AnyRecord {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as AnyRecord : {};
}

function asArray(value: unknown): any[] {
  return Array.isArray(value) ? value : [];
}

function localized(value: unknown, lang: Lang, fallback = ''): string {
  const record = asRecord(value);
  const direct = record[lang];
  if (typeof direct === 'string') return direct;
  const zh = record.zh;
  if (typeof zh === 'string') return zh;
  return typeof value === 'string' ? value : fallback;
}

function localizedList(value: unknown, lang: Lang): string[] {
  const direct = asRecord(value)[lang];
  if (Array.isArray(direct)) return direct.map(String);
  if (Array.isArray(value)) return value.map(String);
  return [];
}

function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function markdownHtml(value: string): string {
  return marked.parse(escapeHtml(value || ''), {
    async: false,
    breaks: false,
    gfm: true,
  }) as string;
}

function MarkdownBlock({ content }: { content: string }) {
  const html = useMemo(() => markdownHtml(content), [content]);
  return <div className="report-markdown" dangerouslySetInnerHTML={{ __html: html }} />;
}

function fmtNumber(value: unknown): string {
  const parsed = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(parsed)) return '—';
  return parsed.toLocaleString('zh-CN');
}

function percent(value: unknown): string {
  const parsed = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(parsed)) return '—';
  return `${Math.round(parsed * 100)}%`;
}

function changePct(value: unknown): string {
  const parsed = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(parsed)) return '—';
  const digits = Math.abs(parsed) >= 10 ? 0 : 1;
  return `${parsed > 0 ? '+' : ''}${parsed.toFixed(digits)}%`;
}

function attentionLabel(value: unknown, lang: Lang): string {
  const key = String(value || 'low').toLowerCase();
  if (lang === 'en') return key;
  return ({ critical: '极高', high: '高', moderate: '中等', low: '低' } as Record<string, string>)[key] || key;
}

function deathLabel(deathReporting: AnyRecord, lang: Lang): string {
  const status = String(deathReporting.status || 'unknown');
  if (status === 'not_reported') return lang === 'zh' ? '死亡数未提供' : 'Deaths not reported';
  if (status === 'unknown') return lang === 'zh' ? '死亡口径未知' : 'Death scope unknown';
  if (status === 'partial') return lang === 'zh' ? '死亡数部分可用' : 'Deaths partially available';
  const total = deathReporting.total_deaths;
  return `${lang === 'zh' ? '死亡' : 'Deaths'} ${fmtNumber(total)}`;
}

function sectionLabel(value: unknown, lang: Lang): string {
  const key = String(value || '');
  const labels: Record<string, { zh: string; en: string }> = {
    decision_summary: { zh: '当前判断', en: 'Current judgement' },
    priority_actions: { zh: '建议动作', en: 'Priority actions' },
    signal_evidence: { zh: '关键证据', en: 'Signal evidence' },
    disease_context: { zh: '疾病背景', en: 'Disease context' },
    data_interpretation_notes: { zh: '数据口径', en: 'Data notes' },
    method_appendix: { zh: '方法附录', en: 'Method appendix' },
  };
  return labels[key]?.[lang] || (lang === 'zh' ? '报告章节' : key || 'Report section');
}

function trendLabel(row: AnyRecord, lang: Lang): string {
  const trend = asRecord(row.trend);
  const direct = trend[lang];
  if (typeof direct === 'string' && direct.trim()) return direct;
  return lang === 'zh' ? '待观察' : 'Watch';
}

function trendDirection(row: AnyRecord): string {
  const direction = String(asRecord(row.trend).direction || 'watch').toLowerCase();
  return direction === 'flat' ? 'stable' : direction;
}

function categoryLabel(value: unknown, lang: Lang): string {
  const key = String(value || 'Other');
  if (lang === 'en') return key || 'Other';
  return ({
    Viral: '病毒性',
    Bacterial: '细菌性',
    Parasitic: '寄生虫性',
    Fungal: '真菌性',
    Prion: '朊病毒',
    Other: '其他',
  } as Record<string, string>)[key] || '其他';
}

function attentionRank(value: unknown): number {
  const key = String(value || 'low').toLowerCase();
  return ({ critical: 4, high: 3, moderate: 2, low: 1 } as Record<string, number>)[key] || 1;
}

function attentionClass(value: unknown): string {
  const key = String(value || 'low').toLowerCase();
  if (key === 'critical') return 'border-red-500/50 bg-red-500/10 text-red-700 dark:text-red-300';
  if (key === 'high') return 'border-orange-500/50 bg-orange-500/10 text-orange-700 dark:text-orange-300';
  if (key === 'moderate') return 'border-amber-500/50 bg-amber-500/10 text-amber-700 dark:text-amber-300';
  return 'border-emerald-500/50 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300';
}

function trendBackgroundClass(row: AnyRecord): string {
  const direction = trendDirection(row);
  if (direction === 'up') return 'bg-red-500/10';
  if (direction === 'down') return 'bg-emerald-500/10';
  if (direction === 'stable') return 'bg-sky-500/10';
  return 'bg-slate-500/10';
}

function changeClass(value: unknown): string {
  const parsed = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(parsed)) return 'text-slate-500';
  if (parsed >= 50) return 'text-red-700 dark:text-red-300';
  if (parsed > 0) return 'text-orange-700 dark:text-orange-300';
  if (parsed < 0) return 'text-emerald-700 dark:text-emerald-300';
  return 'text-slate-600 dark:text-slate-400';
}

function directionFromChange(value: unknown): string {
  const parsed = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(parsed)) return 'watch';
  if (parsed > 0) return 'up';
  if (parsed < 0) return 'down';
  return 'stable';
}

function attentionScore(value: unknown): string {
  const parsed = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(parsed)) return '—';
  return parsed.toFixed(0);
}

function numericSeries(value: unknown): number[] {
  return Array.isArray(value)
    ? value.map((item) => Number(item || 0)).filter((item) => Number.isFinite(item))
    : [];
}

function primaryCaseSeries(record: SparklineSeriesEntry | undefined): number[] {
  if (!record) return [];
  return numericSeries(record.cases);
}

function aggregateCurveByPeriod(record: SparklineSeriesEntry | undefined, period: 'month' | 'year'): number[] {
  if (!record) return [];
  const explicit = period === 'month' ? numericSeries(record.monthly_cases) : numericSeries(record.annual_cases);
  if (explicit.length > 0) return explicit.slice(period === 'month' ? -12 : -10);

  const dates = Array.isArray(record.dates) ? record.dates : [];
  const values = primaryCaseSeries(record);
  if (dates.length === 0 || values.length === 0) {
    return period === 'month' ? values.slice(-24) : [];
  }

  const buckets = new Map<string, number>();
  dates.forEach((date, index) => {
    const key = String(date || '').slice(0, period === 'month' ? 7 : 4);
    if ((period === 'month' && key.length !== 7) || (period === 'year' && key.length !== 4)) return;
    buckets.set(key, (buckets.get(key) || 0) + Number(values[index] || 0));
  });
  return [...buckets.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([, value]) => value)
    .slice(period === 'month' ? -12 : -10);
}

function currentYearCumulativeCases(record: SparklineSeriesEntry | undefined): number | null {
  if (!record) return null;
  const explicit = Number(record.current_year_cumulative_cases);
  if (Number.isFinite(explicit)) return explicit;

  const annual = numericSeries(record.annual_cases);
  if (annual.length > 0) return annual.at(-1) ?? null;

  const dates = Array.isArray(record.dates) ? record.dates : [];
  const values = primaryCaseSeries(record);
  const latestYear = String(dates.at(-1) || '').slice(0, 4);
  if (latestYear.length !== 4 || dates.length === 0 || values.length === 0) return null;
  return dates.reduce((sum, date, index) => (
    String(date || '').startsWith(latestYear) ? sum + Number(values[index] || 0) : sum
  ), 0);
}

function currentYearLabel(record: SparklineSeriesEntry | undefined, lang: Lang): string {
  const year = record?.current_year || (Array.isArray(record?.dates) ? String(record?.dates.at(-1) || '').slice(0, 4) : '');
  if (!year || year.length !== 4) return lang === 'zh' ? '当年累计' : 'YTD cumulative';
  return lang === 'zh' ? `${year}累计` : `${year} YTD`;
}

function curveColorClass(direction?: string, tone: 'monthly' | 'annual' = 'monthly'): string {
  if (tone === 'annual') return 'text-brand-600 dark:text-brand-300';
  if (direction === 'down') return 'text-emerald-600 dark:text-emerald-300';
  if (direction === 'stable') return 'text-sky-600 dark:text-sky-300';
  if (direction === 'up') return 'text-red-600 dark:text-red-300';
  return 'text-slate-500 dark:text-slate-400';
}

function BackgroundCurve({
  values,
  direction,
  tone = 'monthly',
}: {
  values: number[];
  direction?: string;
  tone?: 'monthly' | 'annual';
}) {
  const trendValues = values
    .map((value) => Number(value || 0))
    .filter((value) => Number.isFinite(value));

  if (trendValues.length < 2) return null;

  const width = 180;
  const height = 42;
  const innerWidth = width - 4;
  const min = Math.min(...trendValues);
  const max = Math.max(...trendValues);
  const range = Math.max(1, max - min);
  const denominator = Math.max(trendValues.length - 1, 1);
  const pointPairs = trendValues.map((value, index) => {
    const x = 2 + (index / denominator) * innerWidth;
    const y = height - 4 - ((value - min) / range) * (height - 10);
    return { x, y };
  });
  const points = pointPairs.map(({ x, y }) => `${x},${y}`).join(' ');
  const areaPoints = `2,${height - 2} ${points} ${width - 2},${height - 2}`;

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      className={`pointer-events-none absolute inset-0 h-full w-full ${curveColorClass(direction, tone)}`}
      aria-hidden="true"
    >
      <polygon points={areaPoints} className="fill-current opacity-[0.08]" />
      <polyline
        points={points}
        className="fill-none stroke-current opacity-30"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function ChangeCurveCell({
  value,
  values,
  direction,
  title,
  tone,
  className = '',
}: {
  value: unknown;
  values: number[];
  direction?: string;
  title?: string;
  tone: 'monthly' | 'annual';
  className?: string;
}) {
  return (
    <td
      className={`relative overflow-hidden px-3 py-3 text-right font-medium tabular-nums ${changeClass(value)} ${className}`}
      title={title}
    >
      <BackgroundCurve values={values} direction={direction} tone={tone} />
      <span className="relative z-10 inline-flex min-w-[4.5rem] justify-end bg-white/65 px-1.5 py-0.5 backdrop-blur-[1px] dark:bg-slate-950/55">
        {changePct(value)}
      </span>
    </td>
  );
}

function CasesMoMCell({
  cases,
  change,
  values,
  direction,
  title,
  className = '',
}: {
  cases: unknown;
  change: unknown;
  values: number[];
  direction?: string;
  title?: string;
  className?: string;
}) {
  return (
    <td
      className={`relative overflow-hidden px-3 py-3 text-right tabular-nums ${className}`}
      title={title}
    >
      <BackgroundCurve values={values} direction={direction} tone="monthly" />
      <span className="relative z-10 inline-flex max-w-full items-baseline justify-end gap-1.5 bg-white/65 px-1.5 py-0.5 backdrop-blur-[1px] dark:bg-slate-950/55">
        <span className="font-semibold text-slate-900 dark:text-slate-100">{fmtNumber(cases)}</span>
        <span className={`text-xs font-medium ${changeClass(change)}`}>{changePct(change)}</span>
      </span>
    </td>
  );
}

export default function ReportV4Panel({ report, countryDataUrl, sparklineSeries }: Props) {
  const lang = useLang();
  const document = asRecord(report.report_document_v4 || asRecord(report.metadata).report_document_v4 || report);
  const metrics = asRecord(document.metrics);
  const deathReporting = asRecord(document.death_reporting);
  const dataQuality = asRecord(document.data_quality);
  const attentionRanking = asArray(document.attention_ranking || document.risk_ranking).slice(0, 8);
  const diseaseDirectory = asArray(document.disease_directory);
  const [showAudit, setShowAudit] = useState(false);
  useEffect(() => {
    setShowAudit(new URLSearchParams(window.location.search).has('audit'));
  }, []);
  const sections = asArray(document.sections)
    .filter((section) => showAudit || section.type !== 'method_appendix')
    .sort((a, b) => Number(a.order || 0) - Number(b.order || 0));
  const findings = localizedList(document.key_findings, lang);
  const reportId = String(report.id || metrics.report_id || '');
  const countryCode = String(report.country_code || metrics.country_code || '').toLowerCase();
  const directoryRows = diseaseDirectory.length > 0
    ? diseaseDirectory
    : attentionRanking.map((row) => ({
        ...row,
        slug: String(row.name_en || row.disease_id || 'disease').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''),
        mom_change_pct: row.change_pct,
        trend: { zh: '待观察', en: 'Watch' },
      }));
  const [directoryQuery, setDirectoryQuery] = useState('');
  const [directoryAttentionFilter, setDirectoryAttentionFilter] = useState('all');
  const [directoryTrendFilter, setDirectoryTrendFilter] = useState('all');
  const [countrySeries, setCountrySeries] = useState<Record<string, SparklineSeriesEntry> | undefined>(sparklineSeries);
  useEffect(() => {
    if (sparklineSeries && Object.keys(sparklineSeries).length > 0) {
      setCountrySeries(sparklineSeries);
      return;
    }
    if (!countryDataUrl) return;
    let cancelled = false;
    loadCountryDataset(countryDataUrl)
      .then((dataset) => {
        if (cancelled) return;
        setCountrySeries(dataset.disease_series);
      })
      .catch(() => {
        if (cancelled) return;
        setCountrySeries(undefined);
      });
    return () => {
      cancelled = true;
    };
  }, [countryDataUrl, sparklineSeries]);
  const directoryStats = useMemo(() => {
    const elevatedAttention = directoryRows.filter((row) => attentionRank(row.attention_level || row.risk_level) >= 3).length;
    const rising = directoryRows.filter((row) => trendDirection(row) === 'up').length;
    return { elevatedAttention, rising };
  }, [directoryRows]);
  const visibleDirectoryRows = useMemo(() => {
    const query = directoryQuery.trim().toLowerCase();
    return directoryRows
      .filter((row) => {
        const level = row.attention_level || row.risk_level;
        if (directoryAttentionFilter === 'elevated' && attentionRank(level) < 3) return false;
        if (directoryAttentionFilter === 'moderate' && String(level || '').toLowerCase() !== 'moderate') return false;
        if (directoryAttentionFilter === 'low' && String(level || '').toLowerCase() !== 'low') return false;

        const direction = trendDirection(row);
        if (directoryTrendFilter === 'rising' && direction !== 'up') return false;
        if (directoryTrendFilter === 'falling' && direction !== 'down') return false;
        if (directoryTrendFilter === 'stable' && direction !== 'stable') return false;
        if (directoryTrendFilter === 'watch' && !['watch', 'unknown'].includes(direction)) return false;

        if (!query) return true;
        const searchable = [
          row.disease_id,
          row.name_zh,
          row.name_en,
          row.category,
          categoryLabel(row.category, lang),
          attentionLabel(row.attention_level || row.risk_level, lang),
          trendLabel(row, lang),
        ].join(' ').toLowerCase();
        return searchable.includes(query);
      })
      .sort((left, right) => (
        Number(right.attention_score ?? right.risk_score ?? 0) - Number(left.attention_score ?? left.risk_score ?? 0)
        || attentionRank(right.attention_level || right.risk_level) - attentionRank(left.attention_level || left.risk_level)
        || Number(right.latest_cases || 0) - Number(left.latest_cases || 0)
      ));
  }, [directoryRows, directoryQuery, directoryAttentionFilter, directoryTrendFilter, lang]);

  return (
    <div className="space-y-8">
      <section className="figure-panel">
        <p className="figure-kicker" data-lang-en="Decision brief" data-lang-zh="决策简报">
          {lang === 'zh' ? '决策简报' : 'Decision brief'}
        </p>
        <h2 className="figure-title" data-lang-en="Current judgement and next actions" data-lang-zh="当前判断与下一步动作">
          {lang === 'zh' ? '当前判断与下一步动作' : 'Current judgement and next actions'}
        </h2>
        <div className="mt-5 grid gap-3 sm:grid-cols-4">
          <Metric label={lang === 'zh' ? '病例' : 'Cases'} value={fmtNumber(metrics.total_cases)} />
          <Metric label={lang === 'zh' ? '最新病例' : 'Latest cases'} value={fmtNumber(metrics.latest_cases)} />
          <Metric label={lang === 'zh' ? '死亡口径' : 'Death scope'} value={deathLabel(deathReporting, lang)} />
          <Metric label={lang === 'zh' ? '数据置信度' : 'Data confidence'} value={percent(dataQuality.score)} />
        </div>
        <div className="mt-5">
          <MarkdownBlock content={localized(document.summary, lang)} />
        </div>
        {findings.length > 0 && (
          <ul className="mt-5 space-y-2">
            {findings.map((finding, index) => (
              <li key={`${index}-${finding}`} className="flex gap-3 text-sm leading-6 text-slate-600 dark:text-slate-400">
                <span className="mt-0.5 text-brand-500">→</span>
                <span>{finding}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {directoryRows.length > 0 && (
        <section className="figure-panel">
          <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
            <div className="max-w-2xl">
              <p className="figure-kicker" data-lang-en="Table of contents" data-lang-zh="报告目录">
                {lang === 'zh' ? '报告目录' : 'Table of contents'}
              </p>
              <h2 className="figure-title" data-lang-en="Disease table of contents" data-lang-zh="按疾病进入本期研判">
                {lang === 'zh' ? '按疾病进入本期研判' : 'Disease table of contents'}
              </h2>
            </div>
            <div className="flex w-full flex-wrap items-center justify-between gap-2 border border-slate-200 bg-slate-50/70 px-3 py-2 dark:border-slate-800 dark:bg-slate-950/30 lg:w-auto lg:min-w-[520px]">
              <div className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-500">
                {lang === 'zh' ? '目录概览' : 'Snapshot'}
              </div>
              <div className="flex flex-wrap items-center gap-3 text-xs text-slate-500">
                <DirectoryStat label={lang === 'zh' ? '当前' : 'Shown'} value={`${visibleDirectoryRows.length}/${directoryRows.length}`} />
                <DirectoryStat label={lang === 'zh' ? '高关注' : 'High attention+'} value={fmtNumber(directoryStats.elevatedAttention)} tone="risk" />
                <DirectoryStat label={lang === 'zh' ? '上升' : 'Rising'} value={fmtNumber(directoryStats.rising)} tone="up" />
              </div>
            </div>
          </div>

          <div className="mt-5 grid gap-3 lg:grid-cols-[1fr_180px_180px]">
            <label className="block">
              <span className="sr-only">{lang === 'zh' ? '搜索疾病' : 'Search diseases'}</span>
              <input
                type="search"
                value={directoryQuery}
                onChange={(event) => setDirectoryQuery(event.target.value)}
                placeholder={lang === 'zh' ? '搜索疾病、编号、类别、趋势…' : 'Search disease, ID, category, trend...'}
                className="site-control-input w-full rounded-none border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
              />
            </label>
            <label className="block">
              <span className="sr-only">{lang === 'zh' ? '监测关注优先级筛选' : 'Attention-priority filter'}</span>
              <select
                value={directoryAttentionFilter}
                onChange={(event) => setDirectoryAttentionFilter(event.target.value)}
                className="site-control-input w-full rounded-none border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
              >
                <option value="all">{lang === 'zh' ? '全部关注等级' : 'All attention bands'}</option>
                <option value="elevated">{lang === 'zh' ? '高关注及以上' : 'High attention+'}</option>
                <option value="moderate">{lang === 'zh' ? '中等关注' : 'Moderate attention'}</option>
                <option value="low">{lang === 'zh' ? '低关注' : 'Low attention'}</option>
              </select>
            </label>
            <label className="block">
              <span className="sr-only">{lang === 'zh' ? '趋势筛选' : 'Trend filter'}</span>
              <select
                value={directoryTrendFilter}
                onChange={(event) => setDirectoryTrendFilter(event.target.value)}
                className="site-control-input w-full rounded-none border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
              >
                <option value="all">{lang === 'zh' ? '全部趋势' : 'All trends'}</option>
                <option value="rising">{lang === 'zh' ? '上升' : 'Rising'}</option>
                <option value="falling">{lang === 'zh' ? '下降' : 'Falling'}</option>
                <option value="stable">{lang === 'zh' ? '平稳' : 'Stable'}</option>
                <option value="watch">{lang === 'zh' ? '待观察' : 'Watch'}</option>
              </select>
            </label>
          </div>

          <p className="mt-3 text-xs leading-5 text-slate-500 dark:text-slate-400">
            {lang === 'zh'
              ? '监测关注分（0–100）只用于安排信号复核顺序，由报告病例负担、变化、可用死亡线索、异常标记、历史位置和数据质量组成；未经概率校准，不代表感染、重症、死亡或暴发风险。'
              : 'The 0–100 surveillance attention score only orders signal review. It combines reported burden, change, mortality signals when available, anomaly markers, historical position, and data quality; it is uncalibrated and is not infection, severity, mortality, or outbreak risk.'}
          </p>

          <div className="mt-4 h-[560px] overflow-auto border border-slate-200 dark:border-slate-800">
            <table className="w-[990px] min-w-[990px] table-fixed text-sm">
              <colgroup>
                <col className="w-[44px]" />
                <col className="w-[228px]" />
                <col className="w-[112px]" />
                <col className="w-[162px]" />
                <col className="w-[136px]" />
                <col className="w-[166px]" />
                <col className="w-[142px]" />
              </colgroup>
              <thead className="text-xs text-slate-500 dark:text-slate-400">
                <tr>
                  <th className="sticky top-0 z-10 bg-slate-50 px-3 py-3 text-left dark:bg-slate-900">#</th>
                  <th className="sticky top-0 z-10 bg-slate-50 px-3 py-3 text-left dark:bg-slate-900">{lang === 'zh' ? '疾病目录' : 'Disease'}</th>
                  <th className="sticky top-0 z-10 bg-slate-50 px-3 py-3 text-left dark:bg-slate-900">{lang === 'zh' ? '监测关注级' : 'Attention band'}</th>
                  <th className="sticky top-0 z-10 bg-slate-50 px-3 py-3 text-right dark:bg-slate-900">{lang === 'zh' ? '病例（环比）' : 'Cases (MoM)'}</th>
                  <th className="sticky top-0 z-10 bg-slate-50 px-3 py-3 text-right dark:bg-slate-900">{lang === 'zh' ? '报告期病例' : 'Period cases'}</th>
                  <th className="sticky top-0 z-10 bg-slate-50 px-3 py-3 text-right dark:bg-slate-900">{lang === 'zh' ? '年累计病例' : 'YTD cumulative'}</th>
                  <th className="sticky top-0 z-10 bg-slate-50 px-3 py-3 text-right dark:bg-slate-900">{lang === 'zh' ? '同比' : 'YoY'}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200 text-slate-700 dark:divide-slate-800 dark:text-slate-300">
                {visibleDirectoryRows.length === 0 && (
                  <tr>
                    <td colSpan={7} className="px-3 py-8 text-center text-sm text-slate-500">
                      {lang === 'zh' ? '没有匹配的疾病。' : 'No diseases matched the current filters.'}
                    </td>
                  </tr>
                )}
                {visibleDirectoryRows.map((row, index) => {
                  const diseaseId = String(row.disease_id || '');
                  const rowSeries = countrySeries?.[diseaseId];
                  const monthlyCurve = aggregateCurveByPeriod(rowSeries, 'month');
                  const annualCurve = aggregateCurveByPeriod(rowSeries, 'year');
                  const ytdCases = currentYearCumulativeCases(rowSeries);
                  const diseaseName = lang === 'zh' ? (row.name_zh || row.name_en) : (row.name_en || row.name_zh);
                  const href = countryCode && reportId && row.slug
                    ? `/countries/${countryCode}/reports/${reportId}/${row.slug}/`
                    : undefined;
                  return (
                    <tr key={`${row.disease_id || index}`} className="align-top hover:bg-slate-50/70 dark:hover:bg-slate-900/40">
                      <td className="px-3 py-3 text-slate-500">{index + 1}</td>
                      <td className="w-[228px] px-3 py-3">
                        <div className="w-[204px] whitespace-normal break-words font-medium leading-5 text-slate-900 dark:text-slate-100">
                          {href ? (
                            <a className="text-brand-600 hover:underline dark:text-brand-300" href={href}>
                              {diseaseName}
                            </a>
                          ) : (
                            diseaseName
                          )}
                        </div>
                        <div className="mt-1 flex flex-wrap gap-2 text-xs text-slate-500">
                          {row.disease_id && <span>{row.disease_id}</span>}
                          <span>{categoryLabel(row.category, lang)}</span>
                        </div>
                      </td>
                      <td className="px-3 py-3">
                        <span className={`inline-flex items-center border px-2 py-1 text-xs font-semibold ${attentionClass(row.attention_level || row.risk_level)}`}>
                          {attentionLabel(row.attention_level || row.risk_level, lang)}
                          <span className="ml-1 font-normal opacity-75">{attentionScore(row.attention_score ?? row.risk_score)}</span>
                        </span>
                      </td>
                      <CasesMoMCell
                        cases={row.latest_cases}
                        change={row.mom_change_pct}
                        values={monthlyCurve}
                        direction={trendDirection(row)}
                        title={trendLabel(row, lang)}
                        className={trendBackgroundClass(row)}
                      />
                      <td className="px-3 py-3 text-right tabular-nums" title={lang === 'zh' ? '当前报告窗口内累计病例' : 'Cumulative cases within the report window'}>
                        {fmtNumber(row.total_cases)}
                      </td>
                      <td className="px-3 py-3 text-right tabular-nums" title={currentYearLabel(rowSeries, lang)}>
                        <div>{fmtNumber(ytdCases)}</div>
                        <div className="mt-0.5 text-[10px] font-medium uppercase tracking-[0.08em] text-slate-400">
                          {currentYearLabel(rowSeries, lang)}
                        </div>
                      </td>
                      <ChangeCurveCell
                        value={row.yoy_change_pct}
                        values={annualCurve}
                        direction={directionFromChange(row.yoy_change_pct)}
                        title={lang === 'zh' ? '同比背景：年度发病曲线' : 'YoY background: annual cases curve'}
                        tone="annual"
                        className="bg-brand-500/[0.04]"
                      />
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {sections.map((section) => (
        <section key={section.id || section.order} className="figure-panel">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="figure-kicker">{sectionLabel(section.type, lang)}</p>
              <h2 className="figure-title">{localized(section.title, lang)}</h2>
            </div>
            <span className="mt-2 text-xs uppercase tracking-[0.14em] text-slate-500">
              {section.order ? `${lang === 'zh' ? '第' : 'Section '}${section.order}${lang === 'zh' ? '节' : ''}` : ''}
            </span>
          </div>
          <div className="mt-5">
            <MarkdownBlock content={localized(section.body, lang)} />
          </div>
        </section>
      ))}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-slate-200 bg-slate-50/70 px-4 py-3 dark:border-slate-700/70 dark:bg-slate-900/30">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 truncate text-lg font-semibold text-slate-900 dark:text-slate-100">{value}</div>
    </div>
  );
}

function DirectoryStat({ label, value, tone = 'default' }: { label: string; value: string; tone?: 'default' | 'risk' | 'up' }) {
  const valueClass = tone === 'risk'
    ? 'text-orange-700 dark:text-orange-300'
    : tone === 'up'
      ? 'text-red-700 dark:text-red-300'
      : 'text-slate-900 dark:text-slate-100';

  return (
    <div className="flex min-w-0 items-baseline gap-1.5 whitespace-nowrap">
      <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">{label}</span>
      <span className={`font-serif text-lg font-semibold leading-none tabular-nums ${valueClass}`}>
        {value}
      </span>
    </div>
  );
}
