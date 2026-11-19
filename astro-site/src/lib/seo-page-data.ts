import { resolve } from 'node:path';

import diseasesRaw from '../data/diseases/index.json';
import metaRaw from '../data/meta.json';
import reportsRaw from '../data/reports/index.json';
import { type CountryDiseaseCandidate } from './country-disease-candidates';
import { buildPublishableReports } from './report-routes';
import { toSeoSlug } from './seo';
import { selectDirectoryCandidates, hydrateDirectoryCandidates } from './directory-facts';
import { createSiteDataLoaders } from './site-data-loaders';

const DATA_ROOT = resolve('./src/data');
let indexableDiseaseCache: Array<Record<string, any>> | null = null;
const countryDiseaseCandidateCache = new Map<number, CountryDiseaseCandidate[]>();
let publishableReportCache: ReturnType<typeof buildPublishableReports> | null = null;
let countryDiseaseRouteCache: Array<{ code: string; slug: string }> | null = null;
let countryDiseaseStaticCandidateCache: CountryDiseaseCandidate[] | null = null;

const COUNTRY_DISEASE_ROUTE_LIMIT = 12;
const FULL_COUNTRY_DISEASE_ROUTES = new Set(['cn', 'us']);

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
 * budget. The country directory remains data-driven and links long-tail
 * diseases to their global profile when a local route is not pre-rendered.
 * China and the United States keep every publishable disease route because
 * their country pages are the primary map and directory entry points.
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
  return countryDiseaseRouteCache ??= countryDiseaseStaticCandidates()
    .map(({ code, slug }) => ({ code, slug }));
}

export function publishableReports() {
  if (!publishableReportCache) {
    publishableReportCache = buildPublishableReports(reportIndex, loadReport);
  }
  return publishableReportCache;
}
