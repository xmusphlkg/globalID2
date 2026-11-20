import { resolve } from 'node:path';

import diseasesRaw from '../data/diseases/index.json';
import metaRaw from '../data/meta.json';
import reportsRaw from '../data/reports/index.json';
import { type CountryDiseaseCandidate } from './country-disease-candidates';
import { buildPublishableReports } from './report-routes';
import { toSeoSlug } from './seo';
import { selectDirectoryCandidates, hydrateDirectoryCandidates } from './directory-facts';
import { createSiteDataLoaders } from './site-data-loaders';
import { createCountryPageNavigation } from './country-page-navigation';

const DATA_ROOT = resolve('./src/data');
let indexableDiseaseCache: Array<Record<string, any>> | null = null;
const countryDiseaseCandidateCache = new Map<number, CountryDiseaseCandidate[]>();
let publishableReportCache: ReturnType<typeof buildPublishableReports> | null = null;
let countryDiseaseRouteCache: Array<{ code: string; slug: string }> | null = null;
let countryDiseaseStaticCandidateCache: CountryDiseaseCandidate[] | null = null;

// Country/disease combinations are served by the shared runtime shell. The
// static country and disease directories remain indexable without multiplying
// every locale by every series.
const COUNTRY_DISEASE_ROUTE_LIMIT = 0;
const FULL_COUNTRY_DISEASE_ROUTES = new Set<string>();
let countryNavigationCache: ReturnType<typeof createCountryPageNavigation> | null = null;

export const siteMeta = metaRaw as Record<string, any>;
export const diseaseIndex = diseasesRaw as Array<Record<string, any>>;
export const reportIndex = reportsRaw as Array<Record<string, any>>;

export const { loadCountry, loadDisease, loadKnowledge, loadReport, loadCountryDirectory, loadDiseaseDirectory, loadCountryFacts } = createSiteDataLoaders(DATA_ROOT);

export function diseaseBySlug(slug: string): Record<string, any> | null {
  return diseaseIndex.find(item => toSeoSlug(item.slug) === slug) ?? null;
}

export function indexableDiseases(): Array<Record<string, any>> {
  if (indexableDiseaseCache) return indexableDiseaseCache;
  indexableDiseaseCache = diseaseIndex.filter((disease) => {
    const id = String(disease.disease_id ?? '').toLowerCase();
    return loadDiseaseDirectory(id).indexable;
  });
  return indexableDiseaseCache;
}

export function countryDiseaseCandidates(limit = 50): CountryDiseaseCandidate[] {
  if (!countryDiseaseCandidateCache.has(limit)) {
    countryDiseaseCandidateCache.set(limit, hydrateDirectoryCandidates(selectDirectoryCandidates(siteMeta.countries ?? [], loadCountryFacts, limit), loadCountry));
  }
  return countryDiseaseCandidateCache.get(limit) ?? [];
}

/**
 * Static country/disease pages are deliberately bounded for the Pages file
 * budget. The country directory remains data-driven and links every disease
 * to the shared runtime shell, so new disease records do not require a site
 * rebuild to create another HTML combination.
 */
export function countryDiseaseStaticCandidates(): CountryDiseaseCandidate[] {
  if (countryDiseaseStaticCandidateCache) return countryDiseaseStaticCandidateCache;
  const grouped = new Map<string, ReturnType<typeof selectDirectoryCandidates>>();
  for (const row of selectDirectoryCandidates(siteMeta.countries ?? [], loadCountryFacts, Infinity)) {
    const rows = grouped.get(row.code) ?? [];
    rows.push(row);
    grouped.set(row.code, rows);
  }
  const selected = [...grouped.entries()].flatMap(([code, rows]) =>
    FULL_COUNTRY_DISEASE_ROUTES.has(code) ? rows : rows.slice(0, COUNTRY_DISEASE_ROUTE_LIMIT));
  countryDiseaseStaticCandidateCache = hydrateDirectoryCandidates(selected, loadCountry);
  return countryDiseaseStaticCandidateCache;
}

/** Navigation needs route identities, never the full historical series. */
export function countryDiseaseRouteIdentities() {
  return countryDiseaseRouteCache ??= selectDirectoryCandidates(siteMeta.countries ?? [], loadCountryFacts, Infinity)
    .map(({ code, slug }) => ({ code, slug }));
}

export function countryPageNavigation(country: string, prefix = '') {
  countryNavigationCache ??= createCountryPageNavigation(reportIndex, countryDiseaseRouteIdentities());
  return countryNavigationCache(country, prefix);
}

export function publishableReports() {
  if (!publishableReportCache) {
    publishableReportCache = buildPublishableReports(reportIndex, loadReport);
  }
  return publishableReportCache;
}
