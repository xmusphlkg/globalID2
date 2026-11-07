import assert from 'node:assert/strict';
import { test } from 'node:test';
import { currentSurveillanceSite } from './currentSurveillanceSite.ts';

test('situation surveillance context uses the current summary and never falls back to baked observations', () => {
  const base = { countries: { JP: { name_en: 'Japan', seriesCount: 90, latestDate: '2000-01-01', researchCount: 4, href: '/countries/jp/' } }, diseases: {}, series: { 'JP:D038': { value: 999, date: '2000-01-01', curveHref: '/countries/jp/diseases/influenza/' } } };
  const empty = currentSurveillanceSite(base, undefined, {});
  assert.equal(empty.countries.JP.seriesCount, 0); assert.equal(empty.countries.JP.latestDate, undefined); assert.deepEqual(empty.series, {});
  const current = currentSurveillanceSite(base, { countries: [{ code: 'JP', name_en: 'Japan', disease_count: 2, date_range: { end: '2026-11-01' } }] }, { D038: { reporting_countries: [{ code: 'JP', cases_tail: [10, 20], dates_tail: ['2026-10-01', '2026-11-01'] }] } });
  assert.equal(current.countries.JP.seriesCount, 2); assert.equal(current.countries.JP.researchCount, 4);
  assert.equal(current.series['JP:D038'].value, 20); assert.equal(current.series['JP:D038'].date, '2026-11-01');
  assert.equal(current.series['JP:D038'].curveHref, '/countries/jp/diseases/influenza/');
});
