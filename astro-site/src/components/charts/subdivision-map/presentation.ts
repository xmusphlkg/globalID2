import type { RegionResult, Language } from './model.ts';

export type RegionSortKey = 'name' | 'value' | 'change';
export interface RegionSort { key: RegionSortKey; direction: 'asc' | 'desc'; }
export function relativeChange(row: RegionResult, previous?: RegionResult): number | null {
  if (row.value === null || previous?.value == null || previous.value === 0) return null;
  const value = (row.value - previous.value) / previous.value * 100;
  return Number.isFinite(value) ? value : null;
}
export function sortRegions(rows: readonly RegionResult[], previous: ReadonlyMap<string, RegionResult>, sort: RegionSort, lang: Language, query = ''): RegionResult[] {
  const name = (row: RegionResult) => lang === 'zh' ? row.region.name_zh || row.region.name_en : row.region.name_en;
  const collator = new Intl.Collator(lang, { numeric: true, sensitivity: 'base' });
  const needle = query.trim().toLocaleLowerCase(lang);
  const value = (row: RegionResult) => sort.key === 'change' ? relativeChange(row, previous.get(row.region.code)) : row.value;
  return rows.filter(row => !needle || [row.region.code, row.region.name_en, row.region.name_zh || ''].some(text => text.toLocaleLowerCase(lang).includes(needle))).sort((a, b) => {
    const tie = collator.compare(name(a), name(b)) || a.region.code.localeCompare(b.region.code);
    if (sort.key === 'name') return sort.direction === 'asc' ? tie : -tie;
    const left = value(a), right = value(b);
    // Missing values stay last in both directions; equal values are stable by name.
    if (left === null) return right === null ? tie : 1;
    if (right === null) return -1;
    return (sort.direction === 'asc' ? left - right : right - left) || tie;
  });
}
