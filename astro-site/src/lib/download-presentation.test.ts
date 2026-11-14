import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { downloadPartRecordCount, formatFileSize } from './download-presentation.ts';

test('current download records stay separate from the full historical dataset', () => {
  assert.deepEqual(downloadPartRecordCount({ record_count: 108621, parts: [
    { record_count: 52436 }, { record_count: 9084, is_current: true },
  ] }), { current: 9084, total: 108621 });
});

test('all download surfaces use the same decimal unit', () => {
  assert.equal(formatFileSize(6625805), '6.6 MB');
  assert.equal(formatFileSize(364000), '364 KB');
  assert.equal(formatFileSize(0), '0 B');
  assert.equal(formatFileSize(undefined), '—');
});

test('catalogue row total explains repeated observations beside the metric in every language', () => {
  const source = ['messages.ts', 'DownloadCatalogue.astro'].map(file => readFileSync(new URL(`../components/public/downloads/${file}`, import.meta.url), 'utf8')).join('\n');
  for (const label of ['目录行数（含重复）', 'Catalogue rows (with repeats)', 'Lignes du catalogue (avec doublons)']) {
    assert.ok(source.includes(label), label);
  }
  for (const explanation of ['同一观测可能重复计入', 'Shared observations count again', 'Une même observation peut être comptée plusieurs fois']) {
    assert.ok(source.includes(explanation), explanation);
  }
  assert.match(source, /<dd>\{compact\(totalRecords\)\}<small class="records-note">\{copy\.recordsNote\}<\/small><\/dd>/);
  assert.doesNotMatch(source, /records: 'Enregistrements publiés'/);
});
