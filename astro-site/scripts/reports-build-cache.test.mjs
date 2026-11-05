import assert from 'node:assert/strict';
import { test } from 'node:test';
import { reportsSourceSnapshot } from './reports-build-cache.mjs';

test('isolated report cache shares dependencies without including unrelated page bundles', () => {
  const snapshot = { shared: 'template-v1', articles: {
    'report:jp:1': { fingerprint: 'report-v1', paths: ['countries/jp/reports/1/index.html'] },
    'report-disease:jp:1:flu': { fingerprint: 'flu-v1', paths: [] },
    'report-archive:jp': { fingerprint: 'archive-v1', paths: [] },
    'country:jp': { fingerprint: 'country-v1', paths: [] },
    'disease:flu': { fingerprint: 'disease-v1', paths: [] },
  } };
  const reports = reportsSourceSnapshot(snapshot);
  assert.deepEqual(Object.keys(reports.articles), ['report:jp:1', 'report-disease:jp:1:flu', 'report-archive:jp']);
  assert.deepEqual(reports.articles['report:jp:1'], snapshot.articles['report:jp:1']);
  assert.notEqual(reports.shared, reportsSourceSnapshot({ ...snapshot, shared: 'template-v2' }).shared);
});
