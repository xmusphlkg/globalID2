import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import diseasesRaw from '../data/diseases/index.json';
import metaRaw from '../data/meta.json';
import reportsRaw from '../data/reports/index.json';
import { hasCountryDataSnapshot } from './country-coverage';
import { buildPublishableReports } from './report-routes';
import { isIndexableDisease, toSeoSlug } from './seo';

const DATA_ROOT = resolve('./src/data');

function readJson(path: string): Record<string, any> | null {
  if (!existsSync(path)) return null;
  try {
    return JSON.parse(readFileSync(path, 'utf-8')) as Record<string, any>;
  } catch {
    return null;
  }
}

export const siteMeta = metaRaw as Record<string, any>;
export const diseaseIndex = diseasesRaw as Array<Record<string, any>>;
export const reportIndex = reportsRaw as Array<Record<string, any>>;

export function loadDisease(id: string): Record<string, any> | null {
  return readJson(resolve(DATA_ROOT, 'diseases', `${id.toLowerCase()}.json`));
}

export function loadKnowledge(id: string): Record<string, any> | null {
  return readJson(resolve(DATA_ROOT, 'disease-knowledge', `${id.toLowerCase()}.json`));
}

export function loadCountry(code: string): Record<string, any> | null {
  return readJson(resolve(DATA_ROOT, 'countries', `${code.toLowerCase()}.json`));
}

export function loadReport(id: string): Record<string, any> | null {
  return readJson(resolve(DATA_ROOT, 'reports', `${id}.json`));
}

export function diseaseBySlug(slug: string): Record<string, any> | null {
  return diseaseIndex.find(item => toSeoSlug(item.slug) === slug) ?? null;
}

export function indexableDiseases(): Array<Record<string, any>> {
  return diseaseIndex.filter((disease) => {
    const id = String(disease.disease_id ?? '').toLowerCase();
    const data = loadDisease(id);
    const knowledge = loadKnowledge(id);
    return isIndexableDisease({
      countrySeries: data?.country_series,
      knowledgeStatus: knowledge?.knowledge_status ?? data?.knowledge_status,
      knowledgeSources: knowledge?.knowledge_sources ?? data?.knowledge_sources,
    });
  });
}

export interface CountryDiseaseCandidate {
  code: string;
  country: Record<string, any>;
  series: Record<string, any>;
  slug: string;
}

export function countryDiseaseCandidates(limit = 50): CountryDiseaseCandidate[] {
  const candidates: Array<CountryDiseaseCandidate & { score: number }> = [];
  for (const snapshot of siteMeta.countries ?? []) {
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

export function publishableReports() {
  return buildPublishableReports(reportIndex, loadReport);
}
