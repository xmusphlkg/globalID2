import React, { useState, useMemo } from 'react';

export interface DiseaseComparison {
  disease_id: string;
  name_en: string;
  name_zh: string;
  category: string;
  slug: string;
  cases: number;
  deaths: number;
  cases_prev_month: number;
  deaths_prev_month: number;
  cases_prev_year: number;
  deaths_prev_year: number;
}

interface Props {
  rows: DiseaseComparison[];
  currentMonth: string;   // e.g. "2026-02"
  prevMonth: string;      // e.g. "2026-01"
  prevYearMonth: string;  // e.g. "2025-02"
}

type SortKey = 'name' | 'cases' | 'deaths' | 'delta_pm_cases' | 'delta_py_cases';
type SortDir = 'asc' | 'desc';

function fmtMonth(ym: string): string {
  const [y, m] = ym.split('-');
  return new Date(+y, +m - 1).toLocaleDateString('en-US', { year: 'numeric', month: 'short' });
}

function Delta({ current, prev }: { current: number; prev: number }) {
  if (prev === 0 && current === 0) return <span className="text-slate-600">—</span>;
  const diff = current - prev;
  if (diff === 0) return <span className="text-slate-500">0 (=)</span>;
  const pct = prev > 0 ? ((diff / prev) * 100).toFixed(1) : '∞';
  if (diff > 0) {
    return (
      <span className="text-red-400">
        +{diff.toLocaleString()} (▲{pct}%)
      </span>
    );
  }
  return (
    <span className="text-emerald-400">
      {diff.toLocaleString()} (▼{Math.abs(+pct)}%)
    </span>
  );
}

export default function ReportComparisonTable({ rows, currentMonth, prevMonth, prevYearMonth }: Props) {
  const [showAll, setShowAll] = useState(false);
  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('cases');
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const filtered = useMemo(() => {
    let data = showAll ? rows : rows.filter(r => r.cases > 0 || r.deaths > 0);
    if (search.trim()) {
      const q = search.toLowerCase();
      data = data.filter(
        r => r.name_en.toLowerCase().includes(q) ||
             r.name_zh.includes(q) ||
             r.disease_id.toLowerCase().includes(q) ||
             r.category.toLowerCase().includes(q)
      );
    }
    // Note: sort is applied after filter; total row is derived separately
    return [...data].sort((a, b) => {
      let av = 0, bv = 0;
      if (sortKey === 'name') {
        const cmp = a.name_en.localeCompare(b.name_en);
        return sortDir === 'asc' ? cmp : -cmp;
      } else if (sortKey === 'cases') {
        av = a.cases; bv = b.cases;
      } else if (sortKey === 'deaths') {
        av = a.deaths; bv = b.deaths;
      } else if (sortKey === 'delta_pm_cases') {
        av = a.cases - a.cases_prev_month; bv = b.cases - b.cases_prev_month;
      } else if (sortKey === 'delta_py_cases') {
        av = a.cases - a.cases_prev_year; bv = b.cases - b.cases_prev_year;
      }
      return sortDir === 'asc' ? av - bv : bv - av;
    });
  }, [rows, showAll, search, sortKey, sortDir]);

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  }

  function ColHeader({
    label, sortId, className = ''
  }: { label: string; sortId: SortKey; className?: string }) {
    const active = sortKey === sortId;
    return (
      <th
        className={`site-table-head-cell px-3 py-3 text-left text-xs font-medium uppercase tracking-wider cursor-pointer select-none hover:text-slate-200 transition-colors whitespace-nowrap ${className}`}
        onClick={() => toggleSort(sortId)}
      >
        {label}
        {active && <span className="ml-1 text-brand-400">{sortDir === 'desc' ? '↓' : '↑'}</span>}
      </th>
    );
  }

  return (
    <div>
      {/* Controls */}
      <div className="flex flex-wrap items-center gap-4 mb-4">
        <input
          type="text"
          className="site-control-input border text-sm rounded-lg px-3 py-1.5 w-56 focus:outline-none focus:ring-1 focus:ring-brand-500"
          placeholder="Search disease..."
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
        <label className="flex items-center gap-2 text-sm text-slate-400 cursor-pointer select-none">
          <input type="checkbox" className="rounded border-slate-600 bg-slate-800 text-brand-500 focus:ring-brand-500" checked={showAll} onChange={e => setShowAll(e.target.checked)} />
          <span data-lang-en="Show zero-case diseases" data-lang-zh="显示零病例疾病">
            Show zero-case diseases
          </span>
        </label>
        <span className="text-xs text-slate-600 ml-auto">
          {filtered.length} disease{filtered.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-xl border border-slate-700/50">
        <table className="w-full text-sm">
          <thead className="site-table-head">
            <tr>
              <ColHeader label="Disease" sortId="name" className="min-w-[180px]" />
              <th className="site-table-head-cell px-3 py-3 text-left text-xs font-medium uppercase tracking-wider whitespace-nowrap">
                Category
              </th>
              <ColHeader label="Cases" sortId="cases" />
              <th className="site-table-head-cell px-3 py-3 text-left text-xs font-medium uppercase tracking-wider whitespace-nowrap">
                vs {fmtMonth(prevMonth)}
              </th>
              <th className="site-table-head-cell px-3 py-3 text-left text-xs font-medium uppercase tracking-wider whitespace-nowrap">
                vs {fmtMonth(prevYearMonth)}
              </th>
              <ColHeader label="Deaths" sortId="deaths" />
              <th className="site-table-head-cell px-3 py-3 text-left text-xs font-medium uppercase tracking-wider whitespace-nowrap">
                vs {fmtMonth(prevMonth)}
              </th>
              <th className="site-table-head-cell px-3 py-3 text-left text-xs font-medium uppercase tracking-wider whitespace-nowrap">
                vs {fmtMonth(prevYearMonth)}
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800">
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-3 py-8 text-center text-slate-500">
                  No diseases match your filter.
                </td>
              </tr>
            ) : (
              filtered.map(r => (
                <tr key={r.disease_id} className="site-table-row-hover transition-colors">
                  <td className="px-3 py-2.5">
                    <a
                      href={`/diseases/${r.slug}/`}
                      className="text-brand-400 hover:text-brand-300 hover:underline font-medium"
                    >
                      {r.name_en}
                    </a>
                    {r.name_zh && r.name_zh !== r.name_en && (
                      <span className="block text-xs text-slate-500">{r.name_zh}</span>
                    )}
                    <span className="block text-xs font-mono text-slate-700">{r.disease_id}</span>
                  </td>
                  <td className="px-3 py-2.5">
                    <span className="text-xs px-2 py-0.5 rounded-full bg-slate-700/50 text-slate-400">
                      {r.category || '—'}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-slate-200 font-mono">
                    {r.cases.toLocaleString()}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs">
                    <Delta current={r.cases} prev={r.cases_prev_month} />
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs">
                    <Delta current={r.cases} prev={r.cases_prev_year} />
                  </td>
                  <td className="px-3 py-2.5 text-slate-200 font-mono">
                    {r.deaths.toLocaleString()}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs">
                    <Delta current={r.deaths} prev={r.deaths_prev_month} />
                  </td>
                  <td className="px-3 py-2.5 font-mono text-xs">
                    <Delta current={r.deaths} prev={r.deaths_prev_year} />
                  </td>
                </tr>
              ))
            )}
          </tbody>
          {filtered.length > 0 && (() => {
            const tot = filtered.reduce(
              (acc, r) => ({
                cases: acc.cases + r.cases,
                deaths: acc.deaths + r.deaths,
                cases_pm: acc.cases_pm + r.cases_prev_month,
                deaths_pm: acc.deaths_pm + r.deaths_prev_month,
                cases_py: acc.cases_py + r.cases_prev_year,
                deaths_py: acc.deaths_py + r.deaths_prev_year,
              }),
              { cases: 0, deaths: 0, cases_pm: 0, deaths_pm: 0, cases_py: 0, deaths_py: 0 }
            );
            return (
              <tfoot>
                <tr className="site-table-total-row border-t-2 font-semibold">
                  <td className="px-3 py-3 text-slate-200" colSpan={2}>
                    <span data-lang-en="Total" data-lang-zh="合计">Total</span>
                    <span className="ml-2 text-xs font-normal text-slate-500">({filtered.length} diseases)</span>
                  </td>
                  <td className="px-3 py-3 text-slate-100 font-mono">{tot.cases.toLocaleString()}</td>
                  <td className="px-3 py-3 font-mono text-xs"><Delta current={tot.cases} prev={tot.cases_pm} /></td>
                  <td className="px-3 py-3 font-mono text-xs"><Delta current={tot.cases} prev={tot.cases_py} /></td>
                  <td className="px-3 py-3 text-slate-100 font-mono">{tot.deaths.toLocaleString()}</td>
                  <td className="px-3 py-3 font-mono text-xs"><Delta current={tot.deaths} prev={tot.deaths_pm} /></td>
                  <td className="px-3 py-3 font-mono text-xs"><Delta current={tot.deaths} prev={tot.deaths_py} /></td>
                </tr>
              </tfoot>
            );
          })()}
        </table>
      </div>
    </div>
  );
}
