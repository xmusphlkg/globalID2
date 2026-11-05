import { hasCountryDataSnapshot } from './country-coverage.ts';
import { toSeoSlug } from './seo.ts';

export interface CountryDiseaseCandidate {
  code: string;
  country: Record<string, any>;
  series: Record<string, any>;
  slug: string;
}

export function selectCountryDiseaseCandidates(
  countries: Array<Record<string, any>>,
  loadCountry: (code: string) => Record<string, any> | null,
  limit = 50,
): CountryDiseaseCandidate[] {
  const candidates: Array<CountryDiseaseCandidate & { score: number }> = [];
  for (const snapshot of countries) {
    const code = String(snapshot.code ?? '').toLowerCase();
    if (!code || !hasCountryDataSnapshot(snapshot)) continue;
    const country = loadCountry(code);
    if (!country) continue;
    const sources = [country.source_info?.primary_url, ...(country.source_info?.sources ?? []).map((source: any) => source?.url)]
      .filter((url): url is string => typeof url === 'string' && url.length > 0);
    if (sources.length === 0) continue;
    for (const series of Object.values(country.disease_series ?? {}) as Array<Record<string, any>>) {
      const dates = Array.isArray(series.dates) ? series.dates : [];
      const first = dates[0];
      const last = dates.at(-1);
      const spanDays = first && last ? (new Date(last).getTime() - new Date(first).getTime()) / 86_400_000 : 0;
      const granularity = String(series.period_granularity ?? '').toLowerCase();
      const identity = Number(series.available_series_count ?? 0) > 0
        || (Array.isArray(series.selected_series_codes) && series.selected_series_codes.length > 0)
        || (Array.isArray(series.source_series) && series.source_series.length > 0);
      const enoughHistory = ['annual', 'yearly'].includes(granularity)
        ? dates.length >= 5
        : dates.length >= 24 && spanDays >= 365;
      const slug = toSeoSlug(series.slug);
      if (!identity || !enoughHistory || !slug) continue;
      candidates.push({ code, country, series, slug, score: Number(series.total_cases ?? 0) + dates.length });
    }
  }
  return candidates
    .sort((a, b) => b.score - a.score || `${a.code}/${a.slug}`.localeCompare(`${b.code}/${b.slug}`))
    .slice(0, limit);
}
