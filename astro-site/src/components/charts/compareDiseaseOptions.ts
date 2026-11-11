import type { DiseaseDataset } from './diseaseDataset.ts';

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
