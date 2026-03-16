// src/components/charts/ComparisonTable.tsx
// Sortable, filterable disease comparison table with color-coded change indicators.

import React, { useState, useMemo } from 'react';

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
}

type SortKey = 'name_en' | 'total_cases' | 'total_deaths' | 'latest_cases' | 'category';

const CATEGORY_ORDER = ['Viral', 'Bacterial', 'Parasitic', 'Fungal', 'Other'];

const CATEGORY_STYLES: Record<string, string> = {
  Viral: 'bg-blue-500/15 text-blue-300 ring-1 ring-blue-500/30',
  Bacterial: 'bg-amber-500/15 text-amber-300 ring-1 ring-amber-500/30',
  Parasitic: 'bg-green-500/15 text-green-300 ring-1 ring-green-500/30',
  Fungal: 'bg-purple-500/15 text-purple-300 ring-1 ring-purple-500/30',
};

function Badge({ category }: { category: string }) {
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${CATEGORY_STYLES[category] ?? 'bg-slate-500/15 text-slate-300 ring-1 ring-slate-500/30'}`}>
      {category}
    </span>
  );
}

function fmtNum(n: number) {
  return n.toLocaleString('en-US');
}

export default function ComparisonTable({ rows, countryCode }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('total_cases');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [catFilter, setCatFilter] = useState<string>('All');
  const [search, setSearch] = useState('');
  const [lang] = useState<'en' | 'zh'>(() => {
    if (typeof window !== 'undefined') return (localStorage.getItem('lang') as 'en' | 'zh') || 'en';
    return 'en';
  });

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

  const SortIcon = ({ col }: { col: SortKey }) =>
    sortKey === col ? (
      <span className="ml-1 text-brand-400">{sortDir === 'asc' ? '↑' : '↓'}</span>
    ) : (
      <span className="ml-1 text-slate-700">↕</span>
    );

  return (
    <div className="card overflow-hidden">
      {/* Toolbar */}
      <div className="p-4 border-b border-slate-700/60 flex flex-col sm:flex-row gap-3 items-start sm:items-center">
        <input
          type="search"
          placeholder={lang === 'zh' ? '搜索疾病…' : 'Search diseases…'}
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="site-control-input w-full sm:w-64 px-3 py-1.5 text-sm rounded-lg border focus:outline-none focus:ring-2 focus:ring-brand-500"
        />
        <div className="flex items-center gap-2 flex-wrap">
          {categories.map(cat => (
            <button
              key={cat}
              onClick={() => setCatFilter(cat)}
              className={`px-3 py-1 text-xs font-medium rounded-full border transition-all ${
                catFilter === cat
                  ? 'bg-brand-600 border-brand-500 text-white'
                  : 'site-control-pill'
              }`}
            >
              {cat}
            </button>
          ))}
        </div>
        <span className="ml-auto text-xs text-slate-600 whitespace-nowrap">{displayed.length} diseases</span>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="site-table-head border-b border-slate-700/60 text-left">
              <th
                className="site-table-head-cell px-4 py-3 text-xs font-semibold uppercase tracking-wider cursor-pointer hover:text-slate-300"
                onClick={() => handleSort('name_en')}
              >
                {lang === 'zh' ? '疾病' : 'Disease'} <SortIcon col="name_en" />
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
                onClick={() => handleSort('total_deaths')}
              >
                {lang === 'zh' ? '累计死亡' : 'Total Deaths'} <SortIcon col="total_deaths" />
              </th>
              <th
                className="site-table-head-cell px-4 py-3 text-xs font-semibold uppercase tracking-wider cursor-pointer hover:text-slate-300 text-right"
                onClick={() => handleSort('latest_cases')}
              >
                {lang === 'zh' ? '最新病例' : 'Latest Cases'} <SortIcon col="latest_cases" />
              </th>
              <th className="site-table-head-cell px-4 py-3 text-xs font-semibold uppercase tracking-wider text-right">
                {lang === 'zh' ? '病死率' : 'CFR (%)'}
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {displayed.map(row => {
              const cfr = row.total_cases > 0 ? (row.total_deaths / row.total_cases) * 100 : null;
              const diseasePath = countryCode
                ? `/diseases/${row.slug}/`
                : `/diseases/${row.slug}/`;
              return (
                <tr key={row.disease_id} className="site-table-row-hover transition-colors group">
                  <td className="px-4 py-3">
                    <a href={diseasePath} className="block">
                      <span className="font-medium text-slate-200 group-hover:text-brand-400 transition-colors">
                        {lang === 'zh' ? row.name_zh : row.name_en}
                      </span>
                      <span className="block text-xs text-slate-600 mt-0.5">
                        {lang === 'zh' ? row.name_en : row.name_zh}
                      </span>
                    </a>
                  </td>
                  <td className="px-4 py-3">
                    <Badge category={row.category} />
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-300">
                    {fmtNum(row.total_cases)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-400">
                    {fmtNum(row.total_deaths)}
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    <span className={row.latest_cases > 0 ? 'text-amber-400' : 'text-slate-600'}>
                      {fmtNum(row.latest_cases)}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {cfr != null ? (
                      <span className={cfr > 1 ? 'text-red-400' : cfr > 0.1 ? 'text-amber-400' : 'text-slate-500'}>
                        {cfr.toFixed(2)}%
                      </span>
                    ) : (
                      <span className="text-slate-700">—</span>
                    )}
                  </td>
                </tr>
              );
            })}

            {displayed.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-12 text-center text-slate-600">
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
                  <td className="px-4 py-3 text-slate-200" colSpan={2}>
                    {lang === 'zh' ? '合计' : 'Total'}
                    <span className="ml-2 text-xs font-normal text-slate-500">({displayed.length} {lang === 'zh' ? '种疾病' : 'diseases'})</span>
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-100">{fmtNum(tot.total_cases)}</td>
                  <td className="px-4 py-3 text-right tabular-nums text-slate-300">{fmtNum(tot.total_deaths)}</td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    <span className={tot.latest_cases > 0 ? 'text-amber-400' : 'text-slate-600'}>
                      {fmtNum(tot.latest_cases)}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right tabular-nums">
                    {totCfr != null ? (
                      <span className={totCfr > 1 ? 'text-red-400' : totCfr > 0.1 ? 'text-amber-400' : 'text-slate-500'}>
                        {totCfr.toFixed(2)}%
                      </span>
                    ) : <span className="text-slate-700">—</span>}
                  </td>
                </tr>
              </tfoot>
            );
          })()}
        </table>
      </div>
    </div>
  );
}
