import assert from 'node:assert/strict';
import test from 'node:test';
import { buildChartSourceMeta } from './chartMeta.ts';

test('chart metadata contains source and coverage without stale download links', () => {
  const metadata = buildChartSourceMeta({
    generated_at: '2026-08-01T00:00:00Z',
    date_range: { start: '2020-01-01', end: '2026-08-01' },
    record_count: 42,
    source_info: {
      sources: [{ label: 'Official source', url: 'https://example.test/source' }],
    },
  });

  assert.equal(metadata?.label, 'Official source');
  assert.equal(metadata?.coverage, '2020-01-01 → 2026-08-01');
  assert.equal(metadata?.rowCount, 42);
  assert.equal('downloadHref' in (metadata ?? {}), false);
});
