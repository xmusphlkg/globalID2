import { createHash } from 'node:crypto';
import { readFileSync, statSync } from 'node:fs';
import { resolve } from 'node:path';
import { selectCountryDiseaseCandidates, type CountryDiseaseCandidate } from './country-disease-candidates.ts';
import { hasCountryDataSnapshot } from './country-coverage.ts';
import { isIndexableDisease } from './seo.ts';

export interface CountryFacts {
  generated_at: unknown;
  alertKeys: string[];
  candidates: Array<{ slug: string; score: number | string; diseaseId: string; seriesKey: string }>;
}
export interface DiseaseFacts {
  indexable: boolean;
  generated_at: unknown;
  knowledge_updated_at: unknown;
}
export const factDigest = (value: unknown) => createHash('sha256').update(JSON.stringify(value)).digest('hex');
export function inputSignature(path: string): string {
  try {
    const s = statSync(path, { bigint: true });
    return [s.dev, s.ino, s.size, s.mtimeNs, s.ctimeNs].join(':');
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return 'missing';
    throw error;
  }
}
export function stableSignature(signature: string): boolean {
  return signature === 'missing' || signature.split(':').slice(-2).every(time => BigInt(time) < BigInt(Date.now() - 1000) * 1000000n);
}
export function projectCountry(code: string, record: Record<string, any> | null): CountryFacts | null {
  if (!record) return null;
  const keys = new Map(Object.entries(record.disease_series ?? {}).map(([key, series]) => [series, key]));
  return {
    generated_at: record.generated_at ?? null,
    alertKeys: [...new Set([code, ...['iso2', 'iso3', 'alpha2', 'alpha3', 'code', 'country_code', 'iso_code']
      .map(field => record[field]).filter(value => typeof value === 'string' && value.trim())]
      .map(value => value.trim().toUpperCase()).filter(value => /^[A-Za-z0-9_-]+$/.test(value)))],
    candidates: selectCountryDiseaseCandidates([{ code, data_available: true }], () => record, Infinity)
      .map(({ slug, score, series }) => ({ slug, score: Number.isFinite(score) ? score : String(score),
        diseaseId: String(series.disease_id ?? ''), seriesKey: keys.get(series)! })),
  };
}
export function projectDisease(data: Record<string, any> | null, knowledge: Record<string, any> | null): DiseaseFacts {
  return { generated_at: data?.generated_at ?? null, knowledge_updated_at: knowledge?.knowledge_updated_at ?? null,
    indexable: isIndexableDisease({ countrySeries: data?.country_series,
      knowledgeStatus: knowledge?.knowledge_status ?? data?.knowledge_status,
      knowledgeSources: knowledge?.knowledge_sources ?? data?.knowledge_sources }) };
}
export function selectDirectoryCandidates(countries: Array<Record<string, any>>, load: (code: string) => CountryFacts | null, limit = 50) {
  return countries.filter(hasCountryDataSnapshot).flatMap(row => {
    const code = String(row.code ?? '').toLowerCase();
    return code ? (load(code)?.candidates ?? []).map(item => ({ ...item, code, score: Number(item.score) })) : [];
  }).sort((a, b) => b.score - a.score || `${a.code}/${a.slug}`.localeCompare(`${b.code}/${b.slug}`)).slice(0, limit);
}
export function hydrateDirectoryCandidates(rows: ReturnType<typeof selectDirectoryCandidates>, load: (code: string) => Record<string, any> | null): CountryDiseaseCandidate[] {
  return rows.map(row => ({ code: row.code, slug: row.slug, score: row.score,
    get country() { return load(row.code) ?? {}; },
    get series() { return load(row.code)?.disease_series?.[row.seriesKey] ?? {}; },
  }));
}

/** Only the build wrapper supplies this receipt. Every lookup rechecks input
 * identity; absent, stale or damaged receipts fall back to ordinary readers. */
export function openDirectoryReceipt(dataRoot: string, path = process.env.GIDS_SITE_DIRECTORY_FACTS,
  version = process.env.GIDS_SITE_DIRECTORY_VERSION) {
  let saved: any;
  try {
    if (path && version) saved = JSON.parse(readFileSync(path, 'utf8'));
  } catch { /* Recompute on missing/damaged receipt. */ }
  return {
    get<T>(key: string, paths: string[]): { value: T } | undefined {
      try {
        if (saved?.schema !== 1 || saved.version !== version || saved.dataRoot !== resolve(dataRoot)) return;
        const entry = saved.entries?.[key];
        if (!entry || entry.inputs.length !== paths.length || factDigest(entry.value) !== entry.digest) return;
        if (!entry.inputs.every((input: any, index: number) => input.path === resolve(paths[index])
          && input.stat === inputSignature(paths[index]) && stableSignature(input.stat))) return;
        return { value: entry.value as T };
      } catch { /* Invalid cache data cannot authorize reuse. */ }
    },
  };
}
