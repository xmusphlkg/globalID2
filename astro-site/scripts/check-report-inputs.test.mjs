import assert from 'node:assert/strict';
import { test } from 'node:test';
import { checkReportInputs } from './check-report-inputs.mjs';
import { reportOverviewTitle, reportCoverage } from '../src/lib/report-seo.ts';

const report = (id, start, end) => ({ country: 'jp', id: String(id), detail: {
  country_name_en: 'Japan', country_name_local: '日本', country_name_fr: 'Japon', period_start: start, period_end: end,
} });

test('different weeks in the same month produce distinct report titles in all locales', () => {
  const first = report(63, '2026-10-12', '2026-10-18');
  const second = report(67, '2026-10-19', '2026-10-25');
  assert.deepEqual(checkReportInputs([first, second]), []);
  assert.equal(reportCoverage(first.detail), '2026-10-12–2026-10-18');
  for (const locale of ['en', 'zh', 'fr']) assert.notEqual(
    reportOverviewTitle(first.detail, 'jp', locale), reportOverviewTitle(second.detail, 'jp', locale));
});

test('preflight rejects duplicate and overlong rendered titles before expensive generation', () => {
  const first = report(63, '2026-10-12', '2026-10-18');
  assert.equal(checkReportInputs([first, { ...first, id: '67' }]).filter(error => error.includes('duplicate')).length, 3);
  const long = report(68, '2026-10-19', '2026-10-25');
  long.detail.country_name_local = '非常长的地区名称'.repeat(12);
  assert.ok(checkReportInputs([long]).some(error => error.includes('70 characters')));
});
