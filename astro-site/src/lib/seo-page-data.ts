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

/** Navigation needs route identities, never the full historical series. */
export function countryDiseaseRouteIdentities() {
  return countryDiseaseRouteCache ??= selectDirectoryCandidates(siteMeta.countries ?? [], loadCountryFacts, 50)
    .map(({ code, slug }) => ({ code, slug }));
}

export function publishableReports() {
  if (!publishableReportCache) {
    publishableReportCache = buildPublishableReports(reportIndex, loadReport);
  }
  return publishableReportCache;
}
