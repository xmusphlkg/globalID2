import type { DiseaseDataset } from './diseaseDataset.ts';
import { localizedRegionName } from '../../utils/i18n.ts';

export type CompareDiseaseOption = { id: string; slug: string; href?: string; en: string; zh: string; fr: string; countries?: number };

/** Download archive coverage has its own publication cycle. Comparison choices
 * use current data coverage, without removing previously registered identities. */
export function liveCompareDiseaseOptions(identities: CompareDiseaseOption[], coverage: Record<string, { country_count?: number }> = {}, datasets: Record<string, DiseaseDataset> = {}): CompareDiseaseOption[] {
  return identities.map(identity => {
    const series = datasets[identity.id]?.country_series;
    const count = series ? Object.entries(series).filter(([code, row]) => /^[A-Z]{2}$/.test(code) && row.dates?.length).length : coverage[identity.id]?.country_count;
    return { ...identity, countries: typeof count === 'number' && Number.isFinite(count) && count >= 0 ? count : undefined };
  }).sort((a, b) => (b.countries ?? -1) - (a.countries ?? -1) || a.en.localeCompare(b.en));
}

/** Remove unavailable countries only after the current disease datasets load. */
export function reconcileComparisonCountries(countries: string[], availableCodes: string[]): { countries: string[]; removed: string[] } {
  const available = new Set(availableCodes);
  return { countries: countries.filter(code => available.has(code)), removed: countries.filter(code => !available.has(code)) };
}

/** Keep missing observations in the time axis so ECharts leaves a real gap. */
export function comparisonPoints(dates: string[], values: (number | null | undefined)[]): [string, number | null][] {
  return dates.map((date, index) => [date, Number.isFinite(values[index]) ? values[index] as number : null]);
}

export type CompareAreaMetadata = {
  code: string;
  name_en?: string;
  name_zh?: string;
  name_fr?: string;
  parent_code?: string | null;
  source_info?: { parent_country_code?: string | null } | null;
};
export type CompareAreaOption = {
  code: string;
  parentCode: string | null;
  names: Record<'en' | 'zh' | 'fr', string>;
  selectable: boolean;
};
export type CompareAreaGroup = { area: CompareAreaOption; children: CompareAreaOption[] };

/** Keep the existing c URL parameter while accepting published subdivision keys. */
export function comparisonAreaCodes(value: string | null): string[] {
  return [...new Set((value ?? '').split(',').filter(code => /^[A-Z]{2}(?:-[A-Z0-9]{1,8})?$/.test(code)))].slice(0, 4);
}

/** A group is navigation only unless its own series exists. Never aggregate child data. */
export function comparisonAreaGroups(selected: string[], datasets: Record<string, DiseaseDataset>, catalogue: CompareAreaMetadata[] = []): CompareAreaGroup[] {
  const metadata = new Map(catalogue.map(row => [row.code, row]));
  const codes = new Set(selected.flatMap(id => Object.entries(datasets[id]?.country_series ?? {})
    .filter(([code, row]) => comparisonAreaCodes(code).length && row.dates?.length)
    .map(([code]) => code)));
  const makeArea = (code: string): CompareAreaOption => {
    const row = metadata.get(code);
    const explicitParent = row?.parent_code || row?.source_info?.parent_country_code;
    // Older exports use ISO subdivision keys. Only use their prefix when that
    // parent is already present in the published catalogue or series.
    const prefix = code.includes('-') ? code.split('-')[0] : null;
    const parentCode = explicitParent && explicitParent !== code ? explicitParent
      : prefix && (metadata.has(prefix) || codes.has(prefix)) ? prefix : null;
    const supplied = { en: row?.name_en, zh: row?.name_zh, fr: row?.name_fr };
    return { code, parentCode, selectable: codes.has(code), names: {
      en: localizedRegionName('en', code, supplied),
      zh: localizedRegionName('zh', code, supplied),
      fr: localizedRegionName('fr', code, supplied),
    } };
  };
  const groups = new Map<string, CompareAreaGroup>();
  for (const code of codes) {
    const area = makeArea(code);
    const groupCode = area.parentCode || code;
    const group = groups.get(groupCode) ?? { area: makeArea(groupCode), children: [] };
    if (area.parentCode) group.children.push(area);
    groups.set(groupCode, group);
  }
  const byName = (a: CompareAreaOption, b: CompareAreaOption) => a.names.en.localeCompare(b.names.en);
  return [...groups.values()].sort((a, b) => byName(a.area, b.area))
    .map(group => ({ ...group, children: group.children.sort(byName) }));
}
