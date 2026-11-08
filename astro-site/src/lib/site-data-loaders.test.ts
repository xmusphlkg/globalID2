import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { createSiteDataLoaders } from './site-data-loaders.ts';
import { buildSitemapGroups } from './sitemap-entries.ts';
import { selectCountryDiseaseCandidates } from './country-disease-candidates.ts';

test('compact directories preserve all sitemap entries and avoid retaining series', () => {
  const root = mkdtempSync(join(tmpdir(), 'site-directories-'));
  try {
    for (const dir of ['countries', 'diseases', 'disease-knowledge']) mkdirSync(join(root, dir));
    const save = (path: string, value: unknown) => writeFileSync(join(root, path), JSON.stringify(value));
    const series = { slug: 'flu', available_series_count: 1, period_granularity: 'annual',
      dates: ['2020', '2021', '2022', '2023', '2024'], total_cases: 12 };
    save('countries/aa.json', { generated_at: '2026-10-01', source_info: { primary_url: 'https://example.org' }, disease_series: { flu: series } });
    save('diseases/flu.json', { generated_at: '2026-10-02', country_series: [{ dates: ['2026-10-01'] }] });
    save('disease-knowledge/other.json', { knowledge_status: 'published', knowledge_sources: [{ url: 'https://example.org' }], knowledge_updated_at: '2026-10-03' });
    const loaders = createSiteDataLoaders(root, () => true);
    const options = { meta: { countries: [{ code: 'AA', data_available: true }] },
      diseases: [{ disease_id: 'flu', slug: 'flu' }, { disease_id: 'other', slug: 'other' }, { disease_id: 'empty', slug: 'empty' }],
      reports: [], loadReport: loaders.loadReport, loadCountry: loaders.loadCountry, loadDisease: loaders.loadDisease, loadKnowledge: loaders.loadKnowledge };
    const expected = buildSitemapGroups(options);
    assert.deepEqual(buildSitemapGroups({ ...options, ...loaders }), expected);
    assert.deepEqual(loaders.loadCountryDirectory('AA'), { generated_at: '2026-10-01', candidates: [{ slug: 'flu', score: 17 }] });
    assert.equal(loaders.loadDiseaseDirectory('other').indexable, true);
    assert.equal(loaders.loadDiseaseDirectory('empty').indexable, false);
    // Development readers must see edits rather than build-only summary memoization.
    const dev = createSiteDataLoaders(root, () => false);
    assert.equal(dev.loadCountryDirectory('aa')?.candidates.length, 1);
    save('countries/aa.json', { disease_series: {} });
    assert.equal(dev.loadCountryDirectory('aa')?.candidates.length, 0);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('streaming candidate selection preserves ranking, ties, and limit semantics', () => {
  const countries = Array.from({ length: 20 }, (_, i) => ({ code: `c${i}`, data_available: true }));
  const load = (code: string) => ({ source_info: { primary_url: 'https://example.org' },
    disease_series: Object.fromEntries(Array.from({ length: 8 }, (_, i) => [i, {
      slug: `d${i}`, available_series_count: 1, period_granularity: 'annual',
      dates: ['2020', '2021', '2022', '2023', '2024'], total_cases: (Number(code.slice(1)) * i) % 17,
    }])) });
  const all = selectCountryDiseaseCandidates(countries, load, Infinity);
  for (const limit of [0, 1, 9, 50, 200, -1]) {
    assert.deepEqual(selectCountryDiseaseCandidates(countries, load, limit), all.slice(0, limit));
  }
});
