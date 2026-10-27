// src/components/charts/ComparisonTable.tsx
// Sortable, filterable disease comparison table with optional deaths/CFR columns.

import { useEffect, useMemo, useRef, useState } from 'react';
import type { CountryDatasetSeriesEntry } from './countryDataset';
import { useChartLanguage } from './chartPreferences';
import { useCountryDataset } from './useCountryDataset';
import { toSeoSlug } from '../../lib/seo';
import { localizedDiseaseName } from '../../utils/diseaseNames';

interface DiseaseRow {
  disease_id: string;
  name_en: string;
  name_zh: string;
  name_fr?: string;
  category: string;
  slug: string;
  total_cases: number;
  total_deaths: number;
  latest_cases: number;
  latest_deaths: number;
  incidence_rate?: number | null;
  mortality_rate?: number | null;
}

interface Props {
  rows: DiseaseRow[];
  countryCode?: string;
  series?: Record<string, CountryDatasetSeriesEntry>;
  dataUrl?: string;
  initialLanguage?: 'en' | 'zh' | 'fr';
}

type SortKey = 'name_en' | 'total_cases' | 'total_deaths' | 'latest_cases' | 'category';

const CATEGORY_ORDER = ['Viral', 'Bacterial', 'Parasitic', 'Fungal', 'Other'];
const INITIAL_VISIBLE_ROWS = 24;
const VISIBLE_ROW_INCREMENT = 16;

const CATEGORY_STYLES: Record<string, string> = {
  Viral: 'bg-blue-600/10 text-blue-700 ring-1 ring-blue-600/20 dark:bg-blue-500/12 dark:text-blue-300 dark:ring-blue-500/20',
  Bacterial: 'bg-amber-600/10 text-amber-700 ring-1 ring-amber-600/20 dark:bg-amber-500/12 dark:text-amber-300 dark:ring-amber-500/20',
  Parasitic: 'bg-green-600/10 text-green-700 ring-1 ring-green-600/20 dark:bg-green-500/12 dark:text-green-300 dark:ring-green-500/20',
  Fungal: 'bg-violet-600/10 text-violet-700 ring-1 ring-violet-600/20 dark:bg-violet-500/12 dark:text-violet-300 dark:ring-violet-500/20',
};

const CATEGORY_LABEL_ZH: Record<string, string> = {
  All: '全部',
  Viral: '病毒性',
  Bacterial: '细菌性',
  Parasitic: '寄生虫性',
  Fungal: '真菌性',
  Other: '其他',
};
const CATEGORY_LABEL_FR: Record<string, string> = {
  All: 'Toutes', Viral: 'Virales', Bacterial: 'Bactériennes', Parasitic: 'Parasitaires', Fungal: 'Fongiques', Other: 'Autres',
};

function Badge({ category, lang }: { category: string; lang: 'en' | 'zh' | 'fr' }) {
  return (
    <span className={`inline-flex items-center rounded-none px-2 py-0.5 text-xs font-medium ${CATEGORY_STYLES[category] ?? 'bg-[rgb(var(--surface))] text-[rgb(var(--text))] ring-1 ring-[rgb(var(--border))]'}`}>
      {lang === 'zh' ? CATEGORY_LABEL_ZH[category] ?? category : lang === 'fr' ? CATEGORY_LABEL_FR[category] ?? category : category}
    </span>
  );
}

function fmtNum(n: number | null | undefined) {
  return n == null || !Number.isFinite(n) ? '—' : n.toLocaleString('en-US');
}

function latestReportingContext(entry: CountryDatasetSeriesEntry | undefined) {
  if (!entry) return null;
  const index = entry.cases.reduce<number>((latest, value, i) => value != null && Number.isFinite(value) ? i : latest, -1);
  if (index === -1) return null;
  const start = entry.period_starts?.[index] ?? entry.dates[index];
  const endExclusive = entry.period_ends_exclusive?.[index];
  let period = start ?? '';
  if (start && endExclusive && endExclusive !== start) {
    const endDate = new Date(`${endExclusive}T00:00:00Z`);
    if (Number.isFinite(endDate.getTime())) {
      endDate.setUTCDate(endDate.getUTCDate() - 1);
      period = `${start} – ${endDate.toISOString().slice(0, 10)}`;
    }
  }
  return { period, granularity: entry.point_granularities?.[index] ?? entry.period_granularity ?? null };
}

function granularityLabel(value: string | null, lang: 'en' | 'zh' | 'fr') {
  if (!value) return '';
  const labels: Record<string, [string, string, string]> = {
    daily: ['daily', '日', 'quotidien'], day: ['daily', '日', 'quotidien'],
    weekly: ['weekly', '周', 'hebdomadaire'], week: ['weekly', '周', 'hebdomadaire'],
    monthly: ['monthly', '月', 'mensuel'], month: ['monthly', '月', 'mensuel'],
    quarterly: ['quarterly', '季度', 'trimestriel'], quarter: ['quarterly', '季度', 'trimestriel'],
    annual: ['annual', '年', 'annuel'], yearly: ['annual', '年', 'annuel'], year: ['annual', '年', 'annuel'],
  };
  const label = labels[value.toLowerCase()];
  return label ? label[{ en: 0, zh: 1, fr: 2 }[lang]] : value;
}

function Sparkline({
  diseaseId,
  diseaseName,
  lang,
  series,
}: {
  diseaseId: string;
  diseaseName: string;
  lang: 'en' | 'zh' | 'fr';
  series?: Props['series'];
}) {
  const record = series?.[diseaseId];
  const values = record?.cases ?? [];
  const trendValues = values.slice(-24);
  const observedValues = trendValues.filter(
    (value): value is number => value != null && Number.isFinite(value)
  );

  if (observedValues.length === 0) {
    return <span className="comparison-empty">—</span>;
  }

  const width = 120;
  const height = 30;
  const innerWidth = width - 4;
  const min = Math.min(...observedValues);
  const max = Math.max(...observedValues);
  const range = Math.max(1, max - min);
  const denominator = Math.max(trendValues.length - 1, 1);
  const pointValues = trendValues.map((value, index) => {
      if (value == null || !Number.isFinite(value)) return null;
      const x = 2 + (index / denominator) * innerWidth;
      const y = height - 3 - ((value - min) / range) * (height - 8);
      return `${x},${y}`;
    });
  const segments: string[] = [];
  let currentSegment: string[] = [];
  pointValues.forEach((point) => {
    if (point) {
      currentSegment.push(point);
      return;
    }
    if (currentSegment.length > 0) segments.push(currentSegment.join(' '));
    currentSegment = [];
  });
  if (currentSegment.length > 0) segments.push(currentSegment.join(' '));
  const latestPoint = [...pointValues].reverse().find(Boolean);

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      className="comparison-sparkline"
      role="img"
      aria-label={
        lang === 'zh'
          ? `${diseaseName} 最近 24 个时间点趋势`
          : `${diseaseName} trend across the latest 24 time points`
      }
    >
      <line x1="2" y1={height - 2} x2={width - 2} y2={height - 2} className="comparison-sparkline-track" />
      {segments.map((points, index) => (
        <polyline key={`${points}-${index}`} points={points} className="comparison-sparkline-line" />
      ))}
      {latestPoint && (
        <circle
          cx={latestPoint.split(',')[0]}
          cy={latestPoint.split(',')[1]}
          r="2.8"
          className="comparison-sparkline-dot"
        />
      )}
    </svg>
  );
}

export default function ComparisonTable({ rows, countryCode, series: initialSeries, dataUrl, initialLanguage = 'en' }: Props) {
  void countryCode;
  const hasInitialSeries = Boolean(initialSeries && Object.keys(initialSeries).length > 0);
  const remoteDataset = useCountryDataset(dataUrl, !hasInitialSeries);
  const series = initialSeries ?? remoteDataset.data?.disease_series;
  const [sortKey, setSortKey] = useState<SortKey>('total_cases');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [catFilter, setCatFilter] = useState<string>('All');
  const [search, setSearch] = useState('');
  const [visibleRowCount, setVisibleRowCount] = useState(INITIAL_VISIBLE_ROWS);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const lang = useChartLanguage(initialLanguage);
  const shellRef = useRef<HTMLDivElement>(null);

  const hasDeathsData = useMemo(
    () => rows.some((row) => row.total_deaths > 0 || row.latest_deaths > 0),
    [rows]
  );
  const [showDeaths, setShowDeaths] = useState(hasDeathsData);
  const [showCfr, setShowCfr] = useState(hasDeathsData);

  useEffect(() => {
    if (!hasDeathsData) {
      setShowDeaths(false);
      setShowCfr(false);
      if (sortKey === 'total_deaths') setSortKey('total_cases');
    }
  }, [hasDeathsData, sortKey]);

  useEffect(() => {
    if (!showDeaths && sortKey === 'total_deaths') {
      setSortKey('total_cases');
    }
  }, [showDeaths, sortKey]);

  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(document.fullscreenElement === shellRef.current);
    };

    document.addEventListener('fullscreenchange', handleFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
  }, []);

  function handleSort(key: SortKey) {
    setVisibleRowCount(INITIAL_VISIBLE_ROWS);
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  }

  const displayed = useMemo(() => {
    // Exclude aggregate/summary rows (D999 "Total")
    let filtered = rows.filter(r => r.category !== 'Summary');
    if (catFilter !== 'All') filtered = filtered.filter(r => r.category === catFilter);
    if (search.trim()) {
      const q = search.toLowerCase();
      filtered = filtered.filter(r =>
        r.name_en.toLowerCase().includes(q) || r.name_zh.includes(q) || (r.name_fr ?? '').toLowerCase().includes(q)
      );
    }
    return [...filtered].sort((a, b) => {
      let av: number | string = a[sortKey] as number | string;
      let bv: number | string = b[sortKey] as number | string;
      if (sortKey === 'category') {
        av = CATEGORY_ORDER.indexOf(a.category);
        bv = CATEGORY_ORDER.indexOf(b.category);
      }
      const cmp = typeof av === 'string' ? av.localeCompare(bv as string) : (av as number) - (bv as number);
      return sortDir === 'asc' ? cmp : -cmp;
    });
  }, [rows, sortKey, sortDir, catFilter, search]);
  const visibleRows = displayed.slice(0, visibleRowCount);
  const reportingContexts = displayed.map((row) => latestReportingContext(series?.[row.disease_id])).filter(Boolean);
  const reportingPeriodsDiffer = new Set(reportingContexts.map((context) => context?.period).filter(Boolean)).size > 1;
  const reportingFrequenciesDiffer = new Set(reportingContexts.map((context) => context?.granularity).filter(Boolean)).size > 1;

  const categories = ['All', ...CATEGORY_ORDER];
  const columnCount = 5 + (showDeaths ? 1 : 0) + (showCfr ? 1 : 0);

  const SortIcon = ({ col }: { col: SortKey }) =>
    sortKey === col ? (
      <span className="ml-1 text-brand-400">{sortDir === 'asc' ? '↑' : '↓'}</span>
    ) : (
      <span className="ml-1 text-[rgb(var(--text-muted))]">↕</span>
    );

  async function toggleFullscreen() {
    const shell = shellRef.current;
    if (!shell) return;

    if (document.fullscreenElement === shell) {
      await document.exitFullscreen();
      return;
    }

    if (!document.fullscreenElement) {
      await shell.requestFullscreen();
    }
  }

  return (
    <div ref={shellRef} className={`comparison-shell panel-fullscreen ${isFullscreen ? 'comparison-shell-fullscreen' : ''}`}>
      {/* Toolbar */}
      <div className="comparison-toolbar">
        <input
          id="comparison-disease-search"
          name="comparison-disease-search"
          type="search"
          aria-label={lang === 'zh' ? '搜索疾病' : 'Search diseases'}
          placeholder={lang === 'zh' ? '搜索疾病…' : 'Search diseases…'}
          value={search}
          onChange={e => {
            setSearch(e.target.value);
            setVisibleRowCount(INITIAL_VISIBLE_ROWS);
          }}
          className="site-control-input w-full sm:w-64 px-3 py-1.5 text-sm rounded-none border focus:outline-none focus:ring-2 focus:ring-brand-500"
        />
        <div className="flex items-center gap-2 flex-wrap">
          {categories.map(cat => (
            <button
              key={cat}
              type="button"
              onClick={() => {
                setCatFilter(cat);
                setVisibleRowCount(INITIAL_VISIBLE_ROWS);
              }}
              className={`chart-toggle ${
                catFilter === cat
                  ? 'chart-toggle-active'
                  : ''
              }`}
            >
              {lang === 'zh' ? CATEGORY_LABEL_ZH[cat] ?? cat : cat}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 flex-wrap sm:ml-auto">
          {hasDeathsData && (
            <>
              <button
                type="button"
                onClick={() => setShowDeaths((current) => !current)}
                className={`chart-toggle ${showDeaths ? 'chart-toggle-active' : ''}`}
              >
                {lang === 'zh' ? '显示死亡' : 'Deaths'}
              </button>
              <button
                type="button"
                onClick={() => setShowCfr((current) => !current)}
                className={`chart-toggle ${showCfr ? 'chart-toggle-active' : ''}`}
              >
                {lang === 'zh' ? '显示 CFR' : 'CFR'}
              </button>
            </>
          )}
          <button type="button" onClick={toggleFullscreen} className="chart-link-btn">
            {isFullscreen
              ? (lang === 'zh' ? '退出全屏' : 'Exit full-screen')
              : (lang === 'zh' ? '进入全屏' : 'Enter full-screen')}
          </button>
        </div>
        <span className="chart-chip whitespace-nowrap">
          {lang === 'zh' ? `${displayed.length} 种疾病` : `${displayed.length} diseases`}
        </span>
      </div>
      {remoteDataset.loadError && (
        <div className="chart-data-warning" role="status">
          <span>
            {lang === 'zh'
              ? '趋势数据加载失败，比较数据仍可使用。'
              : 'Trend data failed to load; comparison data remains available.'}
          </span>
          <button type="button" className="chart-link-btn" onClick={remoteDataset.retry}>
            {lang === 'zh' ? '重试趋势数据' : 'Retry trends'}
          </button>
        </div>
      )}
      {reportingContexts.length > 0 && (
        <p className="comparison-cell-secondary px-4 py-2 text-xs" role="note">
          {lang === 'zh'
            ? `最新病例下方标注各疾病的报告期${reportingPeriodsDiffer ? '；报告期不同，数值不可直接按同一时间段比较' : ''}${reportingFrequenciesDiffer ? '；报告频率也不同' : ''}。合计为各疾病最近报告值之和。`
            : lang === 'fr'
              ? `La période de rapport de chaque maladie figure sous les cas${reportingPeriodsDiffer ? ' ; les périodes diffèrent, les valeurs ne portent donc pas toutes sur le même intervalle' : ''}${reportingFrequenciesDiffer ? ' ; les fréquences diffèrent également' : ''}. Le total additionne les dernières valeurs disponibles.`
              : `Each disease’s reporting period appears below its latest cases${reportingPeriodsDiffer ? '; periods differ, so values do not all cover the same interval' : ''}${reportingFrequenciesDiffer ? '; reporting frequencies also differ' : ''}. The total adds each disease’s latest available value.`}
        </p>
      )}

      {/* Table */}
      <div className="comparison-table-wrap">
        <table id="disease-comparison-table" className="comparison-table text-sm">
          <thead>
            <tr className="site-table-head border-b border-[rgb(var(--border))] text-left">
              <th
                className="site-table-head-cell comparison-head-sticky px-4 py-3 text-xs font-semibold uppercase tracking-wider cursor-pointer hover:text-[rgb(var(--text))]"
                onClick={() => handleSort('name_en')}
              >
                {lang === 'zh' ? '疾病' : 'Disease'} <SortIcon col="name_en" />
              </th>
              <th className="site-table-head-cell px-4 py-3 text-xs font-semibold uppercase tracking-wider">
                {lang === 'zh' ? '趋势' : 'Trend'}
              </th>
              <th
                className="site-table-head-cell px-4 py-3 text-xs font-semibold uppercase tracking-wider cursor-pointer hover:text-[rgb(var(--text))]"
                onClick={() => handleSort('category')}
              >
                {lang === 'zh' ? '分类' : 'Category'} <SortIcon col="category" />
              </th>
              <th
                className="site-table-head-cell px-4 py-3 text-xs font-semibold uppercase tracking-wider cursor-pointer hover:text-[rgb(var(--text))] text-right"
                onClick={() => handleSort('total_cases')}
              >
                {lang === 'zh' ? '累计病例' : 'Total Cases'} <SortIcon col="total_cases" />
              </th>
              <th
                className="site-table-head-cell px-4 py-3 text-xs font-semibold uppercase tracking-wider cursor-pointer hover:text-[rgb(var(--text))] text-right"
                onClick={() => handleSort('latest_cases')}
              >
                {lang === 'zh' ? '最新病例' : lang === 'fr' ? 'Cas les plus récents' : 'Latest Cases'} <SortIcon col="latest_cases" />
              </th>
              {showDeaths && (
                <th
                  className="site-table-head-cell px-4 py-3 text-xs font-semibold uppercase tracking-wider cursor-pointer hover:text-[rgb(var(--text))] text-right"
                  onClick={() => handleSort('total_deaths')}
                >
                  {lang === 'zh' ? '累计死亡' : 'Total Deaths'} <SortIcon col="total_deaths" />
                </th>
              )}
              {showCfr && (
                <th className="site-table-head-cell px-4 py-3 text-xs font-semibold uppercase tracking-wider text-right">
                  {lang === 'zh' ? '病死率' : 'CFR (%)'}
                </th>
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-[rgb(var(--border))]">
            {visibleRows.map(row => {
              const cfr = row.total_cases > 0 ? (row.total_deaths / row.total_cases) * 100 : null;
              const diseasePath = `${lang === 'zh' ? '/zh' : lang === 'fr' ? '/fr' : ''}/diseases/${toSeoSlug(row.slug)}/`;
              return (
                <tr key={row.disease_id} className="site-table-row-hover transition-colors group">
                  <td className="comparison-cell-sticky px-4 py-3">
                    <a href={diseasePath} className="block">
                      <span className="comparison-cell-primary font-medium group-hover:text-brand-400 transition-colors">
                        {localizedDiseaseName(row, lang)}
                      </span>
                      <span className="comparison-cell-secondary mt-0.5 block text-xs">
                        {lang === 'zh' ? row.name_en : lang === 'fr' ? row.name_en : row.name_zh}
                      </span>
                    </a>
                  </td>
                  <td className="px-4 py-3">
                    <Sparkline
                      diseaseId={row.disease_id}
                      diseaseName={localizedDiseaseName(row, lang)}
                      lang={lang}
                      series={series}
                    />
                  </td>
                  <td className="px-4 py-3">
                    <Badge category={row.category} lang={lang} />
                  </td>
                  <td className="comparison-cell-number px-4 py-3 text-right tabular-nums">
                    {fmtNum(row.total_cases)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {(() => {
                      const context = latestReportingContext(series?.[row.disease_id]);
                      const frequency = granularityLabel(context?.granularity ?? null, lang);
                      const detail = [context?.period, frequency].filter(Boolean).join(' · ');
                      return (
                        <span className="block">
                          <span className={row.latest_cases > 0 ? 'comparison-cell-accent' : 'comparison-empty'}>{fmtNum(row.latest_cases)}</span>
                          {detail && <span className="comparison-cell-secondary mt-0.5 block whitespace-nowrap text-[10px]" aria-label={lang === 'zh' ? `报告期 ${detail}` : lang === 'fr' ? `Période de rapport : ${detail}` : `Reporting period: ${detail}`}>{detail}</span>}
                        </span>
                      );
                    })()}
                  </td>
                  {showDeaths && (
                    <td className="comparison-cell-number px-4 py-3 text-right tabular-nums">
                      {fmtNum(row.total_deaths)}
                    </td>
                  )}
                  {showCfr && (
                    <td className="px-4 py-3 text-right tabular-nums">
                      {cfr != null ? (
                        <span className={cfr > 1 ? 'comparison-cell-danger' : cfr > 0.1 ? 'comparison-cell-accent' : 'comparison-empty'}>
                          {cfr.toFixed(2)}%
                        </span>
                      ) : (
                        <span className="comparison-empty">—</span>
                      )}
                    </td>
                  )}
                </tr>
              );
            })}

            {displayed.length === 0 && (
              <tr>
                <td colSpan={columnCount} className="comparison-empty px-4 py-12 text-center">
                  {lang === 'zh' ? '未找到匹配数据' : 'No matching diseases found'}
                </td>
              </tr>
            )}
          </tbody>
          {displayed.length > 0 && (() => {
            const tot = displayed.reduce(
              (acc, r) => ({
                total_cases: acc.total_cases + r.total_cases,
                total_deaths: acc.total_deaths + r.total_deaths,
                latest_cases: acc.latest_cases + r.latest_cases,
                latest_deaths: acc.latest_deaths + r.latest_deaths,
              }),
              { total_cases: 0, total_deaths: 0, latest_cases: 0, latest_deaths: 0 }
            );
            const totCfr = tot.total_cases > 0 ? (tot.total_deaths / tot.total_cases) * 100 : null;
            return (
              <tfoot>
                <tr className="site-table-total-row border-t-2 font-semibold">
                  <td className="comparison-cell-primary px-4 py-3" colSpan={3}>
                    {lang === 'zh' ? '合计' : 'Total'}
                    <span className="comparison-cell-secondary ml-2 text-xs font-normal">({displayed.length} {lang === 'zh' ? '种疾病' : 'diseases'})</span>
                  </td>
                  <td className="comparison-cell-primary px-4 py-3 text-right tabular-nums">{fmtNum(tot.total_cases)}</td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    <span className={tot.latest_cases > 0 ? 'comparison-cell-accent' : 'comparison-empty'}>
                      {fmtNum(tot.latest_cases)}
                    </span>
                  </td>
                  {showDeaths && (
                    <td className="comparison-cell-number px-4 py-3 text-right tabular-nums">{fmtNum(tot.total_deaths)}</td>
                  )}
                  {showCfr && (
                    <td className="px-4 py-3 text-right tabular-nums">
                      {totCfr != null ? (
                        <span className={totCfr > 1 ? 'comparison-cell-danger' : totCfr > 0.1 ? 'comparison-cell-accent' : 'comparison-empty'}>
                          {totCfr.toFixed(2)}%
                        </span>
                      ) : <span className="comparison-empty">—</span>}
                    </td>
                  )}
                </tr>
              </tfoot>
            );
          })()}
        </table>
      </div>
      {visibleRows.length < displayed.length && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[rgb(var(--border))] px-4 py-3 text-sm">
          <span className="comparison-cell-secondary" aria-live="polite">
            {lang === 'zh'
              ? `已显示 ${visibleRows.length} / ${displayed.length} 种疾病`
              : `Showing ${visibleRows.length} of ${displayed.length} diseases`}
          </span>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="chart-link-btn"
              aria-controls="disease-comparison-table"
              onClick={() => setVisibleRowCount(current => Math.min(current + VISIBLE_ROW_INCREMENT, displayed.length))}
            >
              {lang === 'zh'
                ? `再显示 ${Math.min(VISIBLE_ROW_INCREMENT, displayed.length - visibleRows.length)} 项`
                : `Show ${Math.min(VISIBLE_ROW_INCREMENT, displayed.length - visibleRows.length)} more`}
            </button>
            <button
              type="button"
              className="chart-link-btn"
              aria-controls="disease-comparison-table"
              onClick={() => setVisibleRowCount(displayed.length)}
            >
              {lang === 'zh' ? '显示全部' : 'Show all'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
