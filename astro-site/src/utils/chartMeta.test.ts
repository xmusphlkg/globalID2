import assert from 'node:assert/strict';
import test from 'node:test';
import {
  buildChartSourceMeta,
  type DownloadEntry,
} from './chartMeta.ts';

test('chart metadata links only to the v2 dataset index', () => {
  const metadata = buildChartSourceMeta({
    dataset_index_path: 'https://data.example/releases/r1/indexes/countries/jp.json',
    record_count: 42,
  });

  assert.equal(
    metadata?.downloadHref,
    'https://data.example/releases/r1/indexes/countries/jp.json',
  );
  assert.equal(metadata?.rowCount, 42);
});

test('legacy JSON and CSV paths are not used as a fallback', () => {
  const legacyEntry = {
    json_path: 'https://legacy.example/countries/jp.json',
    csv_path: 'https://legacy.example/countries/jp.csv',
  } as unknown as DownloadEntry;

  assert.equal(buildChartSourceMeta(legacyEntry)?.downloadHref, undefined);
});
