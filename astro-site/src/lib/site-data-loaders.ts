import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { createJsonFileCache } from './json-file-cache.ts';
import { openDirectoryReceipt, projectCountry, projectDisease, inputSignature, type CountryFacts } from './directory-facts.ts';

export interface CountryDirectory {
  generated_at?: unknown;
  candidates: Array<{ slug: string; score: number }>;
}
export interface DiseaseDirectory {
  indexable: boolean;
  generated_at?: unknown;
  knowledge_updated_at?: unknown;
}

export function createSiteDataLoaders(dataRoot: string, isBuild = () => process.env.GIDS_STATIC_RENDER === '1') {
  const cache = createJsonFileCache({ maxBytes: 128 * 1024 * 1024, maxFileBytes: 64 * 1024 * 1024 });
  const receipt = openDirectoryReceipt(dataRoot);
  const countries = new Map<string, { signature: string; value: CountryFacts | null }>();
  const diseases = new Map<string, { signature: string; value: DiseaseDirectory }>();
  const read = (group: string, id: string): Record<string, any> | null => {
    const path = resolve(dataRoot, group, `${id.toLowerCase()}.json`);
    if (!existsSync(path)) return null;
    try { return cache.read<Record<string, any>>(path); } catch { return null; }
  };
  const loadCountry = (code: string) => read('countries', code);
  const loadDisease = (id: string) => read('diseases', id);
  const loadKnowledge = (id: string) => read('disease-knowledge', id);
  const loadReport = (id: string) => read('reports', id);
  const loadCountryFacts = (code: string): CountryFacts | null => {
    const key = code.toLowerCase();
    const paths = [resolve(dataRoot, 'countries', `${key}.json`)];
    const verified = isBuild() ? receipt.get<CountryFacts | null>(`country:${key}`, paths) : undefined;
    if (verified) return verified.value;
    const signature = inputSignature(paths[0]);
    const cached = countries.get(key);
    if (isBuild() && cached?.signature === signature) return cached.value;
    const summary = projectCountry(key, loadCountry(key));
    if (isBuild()) countries.set(key, { signature, value: summary });
    return summary;
  };
  const loadCountryDirectory = (code: string): CountryDirectory | null => {
    const summary = loadCountryFacts(code);
    return summary ? { generated_at: summary.generated_at,
      candidates: summary.candidates.map(({ slug, score }) => ({ slug, score: Number(score) })) } : null;
  };
  const loadDiseaseDirectory = (id: string): DiseaseDirectory => {
    const key = id.toLowerCase();
    const paths = ['diseases', 'disease-knowledge'].map(group => resolve(dataRoot, group, `${key}.json`));
    const verified = isBuild() ? receipt.get<DiseaseDirectory>(`disease:${key}`, paths) : undefined;
    if (verified) return verified.value;
    const signature = paths.map(inputSignature).join('|');
    const cached = diseases.get(key);
    if (isBuild() && cached?.signature === signature) return cached.value;
    const summary = projectDisease(loadDisease(key), loadKnowledge(key));
    if (isBuild()) diseases.set(key, { signature, value: summary });
    return summary;
  };
  return { loadCountry, loadDisease, loadKnowledge, loadReport, loadCountryDirectory, loadDiseaseDirectory, loadCountryFacts };
}
