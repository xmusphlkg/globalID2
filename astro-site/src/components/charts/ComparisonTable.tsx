// src/components/charts/ComparisonTable.tsx
// Sortable, filterable disease comparison table with optional deaths/CFR columns.

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { loadCountryDataset, type CountryDatasetSeriesEntry } from './countryDataset';

interface DiseaseRow {
  disease_id: string;
  name_en: string;
  name_zh: string;
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
}

type SortKey = 'name_en' | 'total_cases' | 'total_deaths' | 'latest_cases' | 'category';

const CATEGORY_ORDER = ['Viral', 'Bacterial', 'Parasitic', 'Fungal', 'Other'];

const CATEGORY_STYLES: Record<string, string> = {
  Viral: 'bg-blue-600/10 text-blue-700 ring-1 ring-blue-600/20 dark:bg-blue-500/12 dark:text-blue-300 dark:ring-blue-500/20',
  Bacterial: 'bg-amber-600/10 text-amber-700 ring-1 ring-amber-600/20 dark:bg-amber-500/12 dark:text-amber-300 dark:ring-amber-500/20',
  Parasitic: 'bg-green-600/10 text-green-700 ring-1 ring-green-600/20 dark:bg-green-500/12 dark:text-green-300 dark:ring-green-500/20',
  Fungal: 'bg-violet-600/10 text-violet-700 ring-1 ring-violet-600/20 dark:bg-violet-500/12 dark:text-violet-300 dark:ring-violet-500/20',
};

function Badge({ category }: { category: string }) {
  return (
    <span className={`inline-flex items-center rounded-none px-2 py-0.5 text-xs font-medium ${CATEGORY_STYLES[category] ?? 'bg-slate-500/15 text-slate-300 ring-1 ring-slate-500/30'}`}>
      {category}
    </span>
  );
}

function fmtNum(n: number) {
  return n.toLocaleString('en-US');
}

function Sparkline({
  diseaseId,
  diseaseName,
  lang,
  series,
}: {
  diseaseId: string;
  diseaseName: string;
  lang: 'en' | 'zh';
  series?: Props['series'];
}) {
  const record = series?.[diseaseId];
  const values = (record?.weekly_equiv_cases?.some((value) => value > 0)
    ? record.weekly_equiv_cases
    : record?.cases) ?? [];
  const trendValues = values.slice(-24);

  if (trendValues.length === 0) {
    return <span className="comparison-empty">—</span>;
  }

  const width = 120;
  const height = 30;
  const innerWidth = width - 4;
  const min = Math.min(...trendValues);
  const max = Math.max(...trendValues);
  const range = Math.max(1, max - min);
  const denominator = Math.max(trendValues.length - 1, 1);
  const points = trendValues
    .map((value, index) => {
      const x = 2 + (index / denominator) * innerWidth;
      const y = height - 3 - ((value - min) / range) * (height - 8);
      return `${x},${y}`;
    })
    .join(' ');
  const areaPoints = `2,${height - 2} ${points} ${width - 2},${height - 2}`;

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
      <polygon points={areaPoints} className="comparison-sparkline-fill" />
      <polyline points={points} className="comparison-sparkline-line" />
      <circle
        cx={points.split(' ').at(-1)?.split(',')[0]}
        cy={points.split(' ').at(-1)?.split(',')[1]}
        r="2.8"
        className="comparison-sparkline-dot"
      />
    </svg>
  );
}

export default function ComparisonTable({ rows, countryCode, series: initialSeries, dataUrl }: Props) {
  const [series, setSeries] = useState<Record<string, CountryDatasetSeriesEntry> | undefined>(initialSeries);
  const [loadError, setLoadError] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>('total_cases');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [catFilter, setCatFilter] = useState<string>('All');
  const [search, setSearch] = useState('');
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [lang] = useState<'en' | 'zh'>(() => {
    if (typeof window !== 'undefined') return (localStorage.getItem('lang') as 'en' | 'zh') || 'en';
    return 'en';
  });
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

  useEffect(() => {
    if (initialSeries && Object.keys(initialSeries).length > 0) {
      setSeries(initialSeries);
      setLoadError(false);
      return;
    }
    if (!dataUrl) return;

    let cancelled = false;
    loadCountryDataset(dataUrl)
      .then((dataset) => {
        if (cancelled) return;
        setSeries(dataset.disease_series);
        setLoadError(false);
      })
      .catch(() => {
        if (cancelled) return;
        setLoadError(true);
      });

    return () => {
      cancelled = true;
    };
  }, [dataUrl, initialSeries]);

  function handleSort(key: SortKey) {
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
        r.name_en.toLowerCase().includes(q) || r.name_zh.includes(q)
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

  const categories = ['All', ...CATEGORY_ORDER];
  const columnCount = 5 + (showDeaths ? 1 : 0) + (showCfr ? 1 : 0);

  if (loadError) {
    return (
      <div className="chart-shell flex items-center justify-center text-slate-500 text-sm min-h-[160px]">
        {lang === 'zh' ? '表格趋势数据加载失败' : 'Failed to load table trend data'}
      </div>
    );
  }

  const SortIcon = ({ col }: { col: SortKey }) =>
    sortKey === col ? (
      <span className="ml-1 text-brand-400">{sortDir === 'asc' ? '↑' : '↓'}</span>
    ) : (
      <span className="ml-1 text-slate-600">↕</span>
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
          type="search"
          placeholder={lang === 'zh' ? '搜索疾病…' : 'Search diseases…'}
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="site-control-input w-full sm:w-64 px-3 py-1.5 text-sm rounded-none border focus:outline-none focus:ring-2 focus:ring-brand-500"
        />
        <div className="flex items-center gap-2 flex-wrap">
          {categories.map(cat => (
            <button
              key={cat}
              type="button"
              onClick={() => setCatFilter(cat)}
              className={`chart-toggle ${
                catFilter === cat
                  ? 'chart-toggle-active'
                  : ''
              }`}
            >
              {cat}
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

      {/* Table */}
      <div className="comparison-table-wrap">
        <table className="comparison-table text-sm">
          <thead>
            <tr className="site-table-head border-b border-slate-700/60 text-left">
              <th
                className="site-table-head-cell comparison-head-sticky px-4 py-3 text-xs font-semibold uppercase tracking-wider cursor-pointer hover:text-slate-300"
                onClick={() => handleSort('name_en')}
              >
                {lang === 'zh' ? '疾病' : 'Disease'} <SortIcon col="name_en" />
              </th>
              <th className="site-table-head-cell px-4 py-3 text-xs font-semibold uppercase tracking-wider">
                {lang === 'zh' ? '趋势' : 'Trend'}
              </th>
              <th
                className="site-table-head-cell px-4 py-3 text-xs font-semibold uppercase tracking-wider cursor-pointer hover:text-slate-300"
                onClick={() => handleSort('category')}
              >
                {lang === 'zh' ? '分类' : 'Category'} <SortIcon col="category" />
              </th>
              <th
                className="site-table-head-cell px-4 py-3 text-xs font-semibold uppercase tracking-wider cursor-pointer hover:text-slate-300 text-right"
                onClick={() => handleSort('total_cases')}
              >
                {lang === 'zh' ? '累计病例' : 'Total Cases'} <SortIcon col="total_cases" />
              </th>
              <th
                className="site-table-head-cell px-4 py-3 text-xs font-semibold uppercase tracking-wider cursor-pointer hover:text-slate-300 text-right"
                onClick={() => handleSort('latest_cases')}
              >
                {lang === 'zh' ? '最新病例' : 'Latest Cases'} <SortIcon col="latest_cases" />
              </th>
              {showDeaths && (
                <th
                  className="site-table-head-cell px-4 py-3 text-xs font-semibold uppercase tracking-wider cursor-pointer hover:text-slate-300 text-right"
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
          <tbody className="divide-y divide-slate-800">
            {displayed.map(row => {
              const cfr = row.total_cases > 0 ? (row.total_deaths / row.total_cases) * 100 : null;
              const diseasePath = `/diseases/${row.slug}/`;
              return (
                <tr key={row.disease_id} className="site-table-row-hover transition-colors group">
                  <td className="comparison-cell-sticky px-4 py-3">
                    <a href={diseasePath} className="block">
                      <span className="comparison-cell-primary font-medium group-hover:text-brand-400 transition-colors">
                        {lang === 'zh' ? row.name_zh : row.name_en}
                      </span>
                      <span className="comparison-cell-secondary mt-0.5 block text-xs">
                        {lang === 'zh' ? row.name_en : row.name_zh}
                      </span>
                    </a>
                  </td>
                  <td className="px-4 py-3">
                    <Sparkline
                      diseaseId={row.disease_id}
                      diseaseName={lang === 'zh' ? row.name_zh : row.name_en}
                      lang={lang}
                      series={series}
                    />
                  </td>
                  <td className="px-4 py-3">
                    <Badge category={row.category} />
                  </td>
                  <td className="comparison-cell-number px-4 py-3 text-right tabular-nums">
                    {fmtNum(row.total_cases)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    <span className={row.latest_cases > 0 ? 'comparison-cell-accent' : 'comparison-empty'}>
                      {fmtNum(row.latest_cases)}
                    </span>
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
    </div>
  );
}
