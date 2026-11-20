import { test } from 'node:test';
import assert from 'node:assert/strict';
import { downloadPartKey, downloadPartRecordCount, formatFileSize } from './download-presentation.ts';

test('current download records stay separate from the full historical dataset', () => {
  assert.deepEqual(downloadPartRecordCount({ record_count: 108621, parts: [
    { record_count: 52436 }, { record_count: 9084, is_current: true },
  ] }), { current: 9084, total: 108621 });
});

test('cell parts keep a distinct filter key when they share a calendar window', () => {
  assert.equal(downloadPartKey({ id: '2026-2029', country_code: 'CN', disease_id: 'D007' }), 'CN/D007/2026-2029');
  assert.equal(downloadPartKey({ id: '2026-2029' }), '2026-2029');
});

test('all download surfaces use the same decimal unit', () => {
  assert.equal(formatFileSize(6625805), '6.6 MB');
  assert.equal(formatFileSize(364000), '364 KB');
  assert.equal(formatFileSize(0), '0 B');
  assert.equal(formatFileSize(undefined), '—');
});
