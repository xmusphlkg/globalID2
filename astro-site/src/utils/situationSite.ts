/** Build the situation page's join to country surveillance, disease pages, and research. */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { hasCountryDataSnapshot } from '../lib/country-coverage.ts';
import { countryDiseaseCandidates, diseaseIndex, loadCountry, siteMeta } from '../lib/seo-page-data.ts';
import { buildSituationSite, type CountryInput, type DiseaseInput } from '../components/situation/site.ts';
import type { SiteIndex } from './situationTypes.ts';

interface FacetLink {
  count: number;
  url: string;
}

interface ExtractedCatalogue {
  countries: CountryInput[];
  diseases: DiseaseInput[];
  curveKeys: string[];
}

const extractedCache = new Map<string, ExtractedCatalogue>();
let researchFacets: { diseases: Map<string, FacetLink>; countries: Map<string, FacetLink> } | null = null;

function readResearchFacets(): { diseases: Map<string, FacetLink>; countries: Map<string, FacetLink> } {
  if (researchFacets) return researchFacets;
  const diseases = new Map<string, FacetLink>();
  const countries = new Map<string, FacetLink>();
  try {
    const raw = JSON.parse(readFileSync(resolve('./src/data/research/index.json'), 'utf8')) as {
      facets?: {
        diseases?: Array<{ disease_id?: string; count?: number; url?: string }>;
        countries?: Array<{ code?: string; count?: number; url?: string }>;
      };
    };
    for (const item of raw.facets?.diseases ?? []) {
      const id = String(item.disease_id ?? '').toUpperCase();
      if (id && item.url) diseases.set(id, { count: Number(item.count ?? 0), url: String(item.url) });
    }
    for (const item of raw.facets?.countries ?? []) {
      const code = String(item.code ?? '').toUpperCase();
      if (/^[A-Z]{2}$/.test(code) && item.url) countries.set(code, { count: Number(item.count ?? 0), url: String(item.url) });
    }
  } catch {
    // Research links are omitted when the catalogue cannot be read.
  }
  researchFacets = { diseases, countries };
  return researchFacets;
}

function extractCatalogue(
  diseaseIds: string[],
  alertCountries: Array<{ code: string; name_en: string; name_zh?: string | null }>,
): ExtractedCatalogue {
  const wanted = new Set(diseaseIds.map(id => id.toUpperCase()).filter(id => /^D\d+$/.test(id)));
  const alerts = new Map(alertCountries.map(country => [country.code.toUpperCase(), country]));
  const cacheKey = `${[...wanted].sort().join(',')}|${[...alerts.keys()].sort().join(',')}`;
  const cached = extractedCache.get(cacheKey);
  if (cached) return cached;

  const facets = readResearchFacets();
  let curveKeys: string[] = [];
  try {
    curveKeys = countryDiseaseCandidates(50).map(item => `${item.code}:${item.slug}`);
  } catch {
    curveKeys = [];
  }

  const countries: CountryInput[] = [];
  const seen = new Set<string>();
  for (const meta of (siteMeta.countries ?? []) as Array<Record<string, unknown>>) {
    if (meta.location_type !== 'country') continue;
    const code = String(meta.code ?? '').toUpperCase();
    if (!/^[A-Z]{2}$/.test(code) || seen.has(code)) continue;
    seen.add(code);
    const record = loadCountry(code);
    const series: CountryInput['series'] = {};
    const bundle = (record?.disease_series ?? {}) as Record<string, { cases?: Array<number | null>; dates?: string[]; slug?: string }>;
    for (const [id, entry] of Object.entries(bundle)) {
      const diseaseId = id.toUpperCase();
      if (!wanted.has(diseaseId) || !entry) continue;
      series[diseaseId] = { cases: entry.cases, dates: entry.dates, slug: entry.slug };
    }
    const named = alerts.get(code);
    const range = meta.date_range as { end?: string | null } | null | undefined;
    const research = facets.countries.get(code);
    countries.push({
      code,
      name_en: String(meta.name_en ?? meta.name ?? named?.name_en ?? code),
      name_zh: (meta.name_zh as string | null | undefined) ?? named?.name_zh ?? null,
      diseaseCount: Number(meta.disease_count ?? 0),
      latestDate: typeof range?.end === 'string' ? range.end : null,
      page: hasCountryDataSnapshot(meta),
      researchCount: research?.count ?? 0,
      researchUrl: research?.url ?? null,
      series,
    });
  }

  for (const [code, named] of alerts) {
    if (seen.has(code) || !/^[A-Z]{2}$/.test(code)) continue;
    const research = facets.countries.get(code);
    countries.push({
      code,
      name_en: named.name_en || code,
      name_zh: named.name_zh ?? null,
      diseaseCount: 0,
      page: false,
      researchCount: research?.count ?? 0,
      researchUrl: research?.url ?? null,
      series: {},
    });
  }

  const diseases: DiseaseInput[] = [];
  for (const id of wanted) {
    const row = diseaseIndex.find(item => String(item.disease_id ?? '').toUpperCase() === id);
    const research = facets.diseases.get(id);
    if (!row && !research) continue;
    diseases.push({
      id,
      slug: String(row?.slug ?? ''),
      name_en: String(row?.name_en ?? id),
      name_zh: (row?.name_zh as string | null | undefined) ?? null,
      researchCount: research?.count ?? 0,
      researchUrl: research?.url ?? null,
    });
  }

  const extracted = { countries, diseases, curveKeys };
  extractedCache.set(cacheKey, extracted);
  return extracted;
}

export function readSituationSite(options: {
  diseaseIds: string[];
  alertCountries: Array<{ code: string; name_en: string; name_zh?: string | null }>;
  sparkPairs: string[];
  prefix?: string;
}): SiteIndex {
  const extracted = extractCatalogue(options.diseaseIds, options.alertCountries);
  return buildSituationSite({
    ...extracted,
    sparkPairs: options.sparkPairs,
    prefix: options.prefix ?? '',
  });
}
