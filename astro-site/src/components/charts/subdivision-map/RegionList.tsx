import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { Language, RegionResult } from './model';
import { relativeChange, sortRegions, type RegionSort, type RegionSortKey } from './presentation';

const labels = {
  en: { title: 'Regions', search: 'Find a region', area: 'Region', change: 'Change', sort: 'Sort by', empty: 'No matching regions', previous: 'Comparison', unverified: 'Unverified' },
  zh: { title: '地区列表', search: '搜索地区名称或编码', area: '地区', change: '变化', sort: '排序', empty: '没有匹配的地区', previous: '对比范围', unverified: '未核实' },
  fr: { title: 'Régions', search: 'Rechercher une région', area: 'Région', change: 'Variation', sort: 'Trier par', empty: 'Aucune région correspondante', previous: 'Comparaison', unverified: 'Non vérifié' },
};
interface Props {
  rows: RegionResult[]; previous: Map<string, RegionResult>; comparison: boolean;
  lang: Language; selected: string; onSelect: (code: string) => void;
  metricLabel: string; number: (value: number | null | undefined, digits?: number) => string;
  reason: (row: RegionResult) => string; children: ReactNode;
}

export default function RegionList({ rows, previous, comparison, lang, selected, onSelect, metricLabel, number, reason, children }: Props) {
  const t = labels[lang];
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<RegionSort>({ key: 'value', direction: 'desc' });
  const scroll = useRef<HTMLDivElement>(null);
  const activeSort = comparison || sort.key !== 'change' ? sort : { key: 'value', direction: 'desc' } as const;
  const ordered = sortRegions(rows, previous, activeSort, lang, query);
  const name = (row: RegionResult) => lang === 'zh' ? row.region.name_zh || row.region.name_en : row.region.name_en;
  const heading = (key: RegionSortKey, label: string) => <th scope="col" aria-sort={activeSort.key === key ? activeSort.direction === 'asc' ? 'ascending' : 'descending' : 'none'}>
    <button type="button" aria-label={`${t.sort} · ${label}`} onClick={() => setSort({ key, direction: activeSort.key === key ? activeSort.direction === 'asc' ? 'desc' : 'asc' : key === 'name' ? 'asc' : 'desc' })}>
      {label}<span aria-hidden="true">{activeSort.key === key ? activeSort.direction === 'asc' ? '↑' : '↓' : '↕'}</span>
    </button>
  </th>;
  // Selecting on the map reveals the corresponding list row without moving the page.
  useEffect(() => {
    const container = scroll.current;
    const row = container?.querySelector<HTMLElement>('[data-selected]');
    if (!container || !row) return;
    const outer = container.getBoundingClientRect(), inner = row.getBoundingClientRect();
    const header = container.querySelector('thead')?.getBoundingClientRect().height || 0;
    if (inner.top < outer.top + header) container.scrollTop += inner.top - outer.top - header;
    else if (inner.bottom > outer.bottom) container.scrollTop += inner.bottom - outer.bottom;
  }, [selected]);

  return <aside className="subdivision-region-list" aria-label={t.title}>
    <div className="subdivision-region-search"><h3>{t.title}<span>{ordered.length}/{rows.length}</span></h3><input type="search" aria-label={t.search} placeholder={t.search} value={query} onChange={event => setQuery(event.target.value)} /></div>
    <div className="subdivision-region-scroll" ref={scroll}>
      <table><thead><tr>{heading('name', t.area)}{heading('value', metricLabel)}{comparison && heading('change', t.change)}</tr></thead>
        <tbody>{ordered.map(row => {
          const delta = relativeChange(row, previous.get(row.region.code));
          return <tr key={row.region.code} data-region-code={row.region.code} data-selected={row.region.code === selected || undefined} onClick={() => onSelect(row.region.code)}>
            <th scope="row"><button type="button" aria-pressed={row.region.code === selected} onClick={() => onSelect(row.region.code)}><span className="subdivision-region-marker" aria-hidden="true">{row.region.code === selected ? '●' : '○'}</span>{name(row)}</button></th>
            <td title={row.value === null ? reason(row) : comparison ? `${t.previous}: ${number(previous.get(row.region.code)?.value)}` : undefined}>{number(row.value)}{row.provisional && <span aria-label={lang === 'zh' ? '暂定数据' : lang === 'fr' ? 'Provisoire' : 'Provisional'}> *</span>}{row.unverified && <span aria-label={t.unverified}> †</span>}</td>
            {comparison && <td>{delta === null ? '—' : `${delta > 0 ? '+' : ''}${number(delta)}%`}</td>}
          </tr>;
        })}</tbody>
      </table>
      {!ordered.length && <p role="status" className="surveillance-note">{t.empty}</p>}
    </div>
    {children}
  </aside>;
}
