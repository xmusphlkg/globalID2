import assert from 'node:assert/strict';
import { test } from 'node:test';
import { liveCompareDiseaseOptions } from './compareDiseaseOptions.ts';

test('comparison coverage follows live data instead of older download coverage', () => {
  const identities = [{ id: 'A', slug: 'a', en: 'A', zh: '甲', fr: 'A', countries: 90 }, { id: 'B', slug: 'b', en: 'B', zh: '乙', fr: 'B', countries: 0 }];
  const current = liveCompareDiseaseOptions(identities, { A: { country_count: 0 }, B: { country_count: 3 } });
  assert.deepEqual(current.map(row => [row.id, row.countries]), [['B', 3], ['A', 0]]);
  assert.deepEqual(liveCompareDiseaseOptions(identities).map(row => row.countries), [undefined, undefined]);
  assert.equal(identities[0].countries, 90);
  const withSeries = liveCompareDiseaseOptions(identities, { B: { country_count: 3 } }, { B: { country_series: { JP: { dates: ['2026-01-01'] } as any, 'JP-01': { dates: ['2026-01-01'] } as any, US: { dates: [] } as any } } });
  assert.equal(withSeries.find(row => row.id === 'B')?.countries, 1);
});

test('switching disease removes countries that have no current series', async () => {
  const { reconcileComparisonCountries } = await import('./compareDiseaseOptions.ts');
  assert.deepEqual(reconcileComparisonCountries(['AU', 'AT'], ['AU', 'JP']), { countries: ['AU'], removed: ['AT'] });
  assert.deepEqual(reconcileComparisonCountries(['AU', 'JP'], ['AU', 'JP']), { countries: ['AU', 'JP'], removed: [] });
});

test('missing observations remain explicit gaps rather than connecting adjacent dates', async () => {
  const { comparisonPoints } = await import('./compareDiseaseOptions.ts');
  assert.deepEqual(comparisonPoints(['2026-01-01', '2026-01-08', '2026-01-15', '2026-01-22'], [10, null, 0]), [
    ['2026-01-01', 10], ['2026-01-08', null], ['2026-01-15', 0], ['2026-01-22', null],
  ]);
});
