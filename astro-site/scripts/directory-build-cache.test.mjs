import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync, statSync, utimesSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { openDirectoryBuildCache } from './directory-build-cache.mjs';
import { openDirectoryReceipt, projectCountry, selectDirectoryCandidates, hydrateDirectoryCandidates } from '../src/lib/directory-facts.ts';
import { selectCountryDiseaseCandidates } from '../src/lib/country-disease-candidates.ts';

const country = (cases = 12) => ({ generated_at: '2026-11-06', iso2: 'AA', source_info: { primary_url: 'https://example.org' },
  disease_series: { D1: { slug: 'flu', disease_id: 'D1', available_series_count: 1,
    period_granularity: 'annual', dates: ['2020', '2021', '2022', '2023', '2024'], total_cases: cases } } });
const hash = path => createHash('sha256').update(readFileSync(path)).digest('hex');

test('persistent facts avoid JSON reads; edits, deletion, code versions and damaged values invalidate', () => {
  const root = mkdtempSync(join(tmpdir(), 'directory-facts-'));
  try {
    mkdirSync(join(root, 'countries')); mkdirSync(join(root, 'diseases')); mkdirSync(join(root, 'disease-knowledge'));
    const file = join(root, 'countries/aa.json'); const path = join(root, 'cache.json');
    writeFileSync(file, JSON.stringify(country()));
    let reads = 0;
    const options = { path, version: 'v1', hash, read: path => { reads++; return JSON.parse(readFileSync(path)); } };
    const cold = openDirectoryBuildCache(root, options); const expected = cold.country('aa'); cold.disease('d1'); cold.save();
    const warm = openDirectoryBuildCache(root, options); assert.deepEqual(warm.country('aa'), expected); warm.disease('d1'); warm.save();
    assert.equal(reads, 1); assert.equal(warm.stats.misses, 0);
    // Even a same-size edit with a restored mtime must rebuild the summary.
    const before = statSync(file); writeFileSync(file, JSON.stringify(country(13))); utimesSync(file, before.atime, before.mtime);
    const edited = openDirectoryBuildCache(root, options); assert.equal(edited.country('aa').candidates[0].score, 18); edited.save();
    assert.equal(reads, 2);
    const corrupt = JSON.parse(readFileSync(path)); corrupt.entries['country:aa'].value.candidates[0].score = 999;
    writeFileSync(path, JSON.stringify(corrupt));
    const repaired = openDirectoryBuildCache(root, options); assert.equal(repaired.country('aa').candidates[0].score, 18); repaired.save();
    assert.equal(reads, 3);
    const changedCode = openDirectoryBuildCache(root, { ...options, version: 'v2' }); changedCode.country('aa');
    assert.equal(reads, 4);
    rmSync(file); assert.equal(openDirectoryBuildCache(root, options).country('aa'), null);
    writeFileSync(join(root, 'disease-knowledge/d1.json'), JSON.stringify({ knowledge_status: 'published', knowledge_sources: [{ url: 'https://example.org' }] }));
    assert.equal(openDirectoryBuildCache(root, options).disease('d1').indexable, true);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('render receipts reject changed files, wrong roots/versions and malformed entries; racing reads fail', () => {
  const root = mkdtempSync(join(tmpdir(), 'directory-receipt-'));
  const realNow = Date.now;
  try {
    mkdirSync(join(root, 'countries')); const file = join(root, 'countries/aa.json'); const path = join(root, 'cache.json');
    writeFileSync(file, JSON.stringify(country()));
    const options = { path, version: 'v1', hash };
    const cache = openDirectoryBuildCache(root, options); const value = cache.country('aa'); cache.save();
    assert.equal(openDirectoryReceipt(root, path, 'v1').get('country:aa', [file]), undefined, 'recent files are revalidated');
    Date.now = () => realNow() + 2000;
    const receipt = openDirectoryReceipt(root, path, 'v1');
    assert.deepEqual(receipt.get('country:aa', [file]), { value });
    assert.equal(openDirectoryReceipt(root, path, 'v2').get('country:aa', [file]), undefined);
    assert.equal(openDirectoryReceipt(join(root, 'other'), path, 'v1').get('country:aa', [file]), undefined);
    writeFileSync(file, JSON.stringify(country(999)));
    assert.equal(receipt.get('country:aa', [file]), undefined);
    writeFileSync(path, '{"schema":1,"version":"v1"}');
    assert.equal(openDirectoryReceipt(root, path, 'v1').get('country:aa', [file]), undefined);
    assert.throws(() => openDirectoryBuildCache(root, { ...options, read: path => {
      const data = JSON.parse(readFileSync(path)); writeFileSync(path, JSON.stringify(country(98))); return data;
    } }).country('aa'), /changed while reading/);
  } finally { Date.now = realNow; rmSync(root, { recursive: true, force: true }); }
});

test('compact ranking matches full records and metadata consumers never load histories', () => {
  const countries = Array.from({ length: 20 }, (_, i) => ({ code: `c${i}`, data_available: true }));
  const records = Object.fromEntries(countries.map(({ code }, i) => [code, country(i % 7)]));
  const load = code => records[code];
  for (const limit of [0, 1, 5, 50, -1, Infinity]) {
    const rows = selectDirectoryCandidates(countries, code => projectCountry(code, load(code)), limit);
    let reads = 0;
    const lazy = hydrateDirectoryCandidates(rows, code => { reads++; return load(code); });
    assert.deepEqual(lazy.map(({ code, slug, score }) => ({ code, slug, score })),
      selectCountryDiseaseCandidates(countries, load, limit).map(({ code, slug, score }) => ({ code, slug, score })));
    assert.equal(reads, 0);
    assert.deepEqual(lazy, selectCountryDiseaseCandidates(countries, load, limit));
    if (lazy.length) assert.ok(reads > 0);
  }
});
