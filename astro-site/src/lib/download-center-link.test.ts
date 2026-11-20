import assert from 'node:assert/strict';
import test from 'node:test';
import { downloadCenterHref, downloadScopeFromSummary } from './download-center-link.ts';

test('country, disease and report entry points preserve their scope and locale', () => {
  assert.equal(downloadCenterHref('zh', { country: 'jp' }), '/zh/downloads/?country=JP');
  assert.equal(downloadCenterHref('fr', { disease: 'd038' }), '/fr/downloads/?disease=D038');
  assert.equal(downloadCenterHref('en', { country: 'jp', disease: 'd038' }, true),
    'https://globalinfectiousdisease.com/downloads/?country=JP&disease=D038');
  assert.equal(downloadCenterHref('en', { country: 'au-nsw' }), '/downloads/?country=AU-NSW');
});

test('summary addresses resolve scope without requiring the archive manifest', () => {
  assert.deepEqual(downloadScopeFromSummary('/site-data/countries/jp-summary.json'), { country: 'jp' });
  assert.deepEqual(downloadScopeFromSummary('/site-data/diseases/d038-summary.json'), { disease: 'd038' });
  assert.deepEqual(downloadScopeFromSummary('/site-data/countries/jp.json'), {});
  assert.equal(downloadCenterHref('zh', { country: 'JP&disease=FAKE' }), '/zh/downloads/');
});
