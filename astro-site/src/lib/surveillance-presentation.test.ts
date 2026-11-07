import assert from 'node:assert/strict';
import test from 'node:test';
import { countryLabel, diseaseLabel, cadence, sourceEntries,  } from './surveillance-presentation.ts';
import { nationalRows, subdivisionRows, officialSourceCount } from './surveillance-coverage.ts';
import { curveSegments } from './home-influenza-model.ts';
import { latestSnapshot } from './surveillance-snapshot.ts';
import { monthOfWeek, weekOfDay, monthTotals } from '../components/situation/archive-model.ts';

test('national coverage excludes subdivisions and unavailable datasets', () => {
  const source_info = { sources: [{ label: 'Authority', url: 'https://example.test', cadence: 'weekly' }] };
  const data = { countries: [{ code: 'JP', data_available: true, source_info }, { code: 'JP-13', parent_code: 'JP', data_available: true, source_info }, { code: 'CN', data_available: false }] };
  assert.deepEqual(nationalRows(data).map((row: { code: string }) => row.code), ['JP']);
  assert.equal(subdivisionRows(data).length, 1);
  assert.equal(officialSourceCount(data), 1);
});
test('source deduplication retains jurisdiction context and distinct definitions', () => {
  const source = { label: 'Authority', url: 'https://example.test', cadence: 'weekly', description: 'National count' };
  const rows = sourceEntries([{ country_code: 'JP', sources: [source, source] }, { country_code: 'JP-13', sources: [source, { ...source, description: 'Sentinel count' }] }]);
  assert.equal(rows.length, 2);
  assert.deepEqual(rows[0].countries.map((row: { code: string }) => row.code), ['JP', 'JP-13']);
});
test('French labels and reporting cadence use the existing translations', () => {
  assert.equal(countryLabel({ code: 'JP', name_en: 'Japan' }, 'fr'), 'Japon');
  assert.equal(diseaseLabel({ disease_id: 'D038', slug: 'influenza', name_en: 'Influenza' }, 'fr'), 'Grippe');
  assert.equal(cadence('WEEKLY', 'zh'), '每周');
  assert.equal(cadence('monthly', 'fr'), 'Mensuelle');
});
test('hero curves preserve missing gaps, real zeros, and relative peaks', () => {
  const segments = curveSegments([0, 10, null, 5, 0], 738, 224);
  assert.equal(segments.length, 2);
  assert.equal(segments[0], '42.0,738.0 381.0,514.0');
  assert.deepEqual(curveSegments([null, null], 738, 224), []);
});
test('snapshot comparison requires consecutive reported periods and preserves a latest zero', () => {
  const row = { dates_tail: ['2026-10-04', '2026-10-11'], cases_tail: [10, 0], period_granularity: 'weekly', comparability: 'comparable' };
  assert.deepEqual(latestSnapshot(row), { date: '2026-10-11', value: 0, change: -100 });
  assert.equal(latestSnapshot({ ...row, dates_tail: ['2026-09-01', '2026-10-11'] })?.change, null);
  assert.equal(latestSnapshot({ ...row, cases_tail: [null, 0] })?.change, null);
  assert.equal(latestSnapshot({ ...row, comparability: 'not_comparable' })?.change, null);
});
test('archive boundaries use ISO weeks and monthly totals count distinct outbreaks', () => {
  assert.equal(weekOfDay('2021-01-01'), '2020-W53');
  assert.equal(weekOfDay('2026-11-07'), '2026-W45');
  assert.equal(monthOfWeek('2020-W53'), '2020-12');
  const totals = monthTotals([{ period_key: '2026-W44', new_outbreaks: ['a'], updated_outbreaks: ['b'], new_places: 2, alerts: 3 }, { period_key: '2026-W45', new_outbreaks: ['c'], updated_outbreaks: ['b'], new_places: 1, alerts: 5 }]);
  assert.equal(totals.updated_outbreaks.length, 1);
  assert.equal(totals.alerts, 8);
});
