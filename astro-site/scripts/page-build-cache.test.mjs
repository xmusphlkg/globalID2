import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { test } from 'node:test';
import { pageSourceSnapshot } from './page-build-cache.mjs';
import { finalizeResearchSnapshot, planResearchBuild } from './research-build-cache.mjs';

test('page dependencies isolate research edits and track navigation, translations, alerts and downloads', () => {
  const root = mkdtempSync(resolve(tmpdir(), 'gids-page-deps-'));
  const put = (path, value) => {
    const target = resolve(root, path);
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, JSON.stringify(value));
  };
  const snapshot = () => pageSourceSnapshot({ dataRoot: root, shared: 'templates' });
  const changed = (a, b) => Object.keys(b.articles).filter(key => a.articles[key]?.fingerprint !== b.articles[key].fingerprint);
  try {
    put('research/routes.json', { facets: { countries: [{ slug: 'jp', name_en: 'Japan' }], topics: [{ slug: 'vaccines', name: 'Vaccines' }], weeks: [{ week: '2026-W01' }] } });
    put('meta.json', { generated_at: 'before', countries: [{ code: 'JP', data_available: true }, { code: 'FR', record_count: 1 }, { code: 'XX' }] });
    put('diseases/index.json', [{ disease_id: 'D001', slug: 'Test & Disease' }]);
    put('countries/jp.json', { total_cases: 1 });
    put('countries/fr.json', { total_cases: 2 });
    put('research/countries/jp.json', { articles: ['a'] });
    put('research/topics/vaccines.json', { articles: ['a'] });
    put('research/weekly/2026-W01.json', { articles: ['a'] });
    put('situation/international/by-country/JP.json', { schema_version: 'v1', as_of: 'before', alerts: [{ title: 'alert' }] });
    const initial = snapshot();
    assert.deepEqual(initial.articles['disease:D001'].paths, ['diseases/test-and-disease/index.html', 'zh/diseases/test-and-disease/index.html', 'fr/diseases/test-and-disease/index.html']);
    assert.equal(initial.articles['country:xx'], undefined);
    put('research/topics/vaccines.json', { articles: ['a', 'b'] });
    assert.deepEqual(changed(initial, snapshot()), ['research:topics:vaccines']);
    let before = snapshot();
    put('countries/jp.json', { total_cases: 100 });
    assert.deepEqual(changed(before, snapshot()), ['country:jp', 'country:fr', 'disease:D001']);
    before = snapshot();
    put('situation/international/by-country/JP.json', { schema_version: 'v1', as_of: 'after', alerts: [{ title: 'alert' }] });
    assert.deepEqual(changed(before, snapshot()), []);
    put('situation/international/by-country/JP.json', { schema_version: 'v1', alerts: [{ title: 'new alert' }] });
    assert.deepEqual(changed(before, snapshot()), ['country:jp', 'country:fr']);
    before = snapshot();
    put('downloads.json', { countries: [{ code: 'JP', generated_at: 'new' }] });
    assert.deepEqual(changed(before, snapshot()), ['country:jp']);
    before = snapshot();
    put('disease-knowledge/d001.json', { official_summary_fr: 'Nouveau résumé' });
    assert.deepEqual(changed(before, snapshot()), ['disease:D001']);
    before = snapshot();
    put('research/diseases/d001.json', { articles: ['new article'] });
    assert.deepEqual(changed(before, snapshot()), ['disease:D001']);
    before = snapshot();
    put('research/weekly/2026-W01.json', { articles: ['a'], byline: { reviewer: { name: 'Reviewer' } } });
    assert.deepEqual(changed(before, snapshot()), ['research:weekly:2026-W01']);
    put('research/routes.json', { facets: { topics: [{ slug: '../escape' }] } });
    assert.throws(snapshot, /Unsafe/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('all locales must be intact and deleted routes are never restored', () => {
  const root = mkdtempSync(resolve(tmpdir(), 'gids-page-reuse-'));
  const outputRoot = resolve(root, 'dist');
  const cache = resolve(root, 'cache');
  const snapshot = { schema: 1, shared: 'same', articles: {
    'country:jp': { fingerprint: 'jp', paths: ['countries/jp/index.html', 'fr/countries/jp/index.html', 'zh/countries/jp/index.html'] },
    'country:fr': { fingerprint: 'fr', paths: ['countries/fr/index.html'] },
  } };
  try {
    for (const page of Object.values(snapshot.articles)) for (const path of page.paths) {
      mkdirSync(dirname(resolve(outputRoot, path)), { recursive: true });
      writeFileSync(resolve(outputRoot, path), path);
    }
    finalizeResearchSnapshot(snapshot, { outputRoot, pageCache: cache, targetManifest: resolve(root, 'manifest.json'), sourcePending: resolve(root, 'pending.json') });
    const next = { ...snapshot, articles: { 'country:jp': snapshot.articles['country:jp'] } };
    const plan = () => planResearchBuild({ snapshot: next, previous: snapshot, outputRoot: cache, incremental: true });
    assert.deepEqual(plan().reused, ['country:jp']);
    writeFileSync(resolve(cache, 'fr/countries/jp/index.html'), 'corruption');
    assert.deepEqual(plan().selected, ['country:jp']);
    rmSync(resolve(cache, 'fr/countries/jp/index.html'));
    assert.deepEqual(plan().selected, ['country:jp']);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
