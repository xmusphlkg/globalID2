import assert from 'node:assert/strict';
import test from 'node:test';
import {
  MONTH_NAMES,
  buildYearSummaries,
  collectYears,
  getRecentYears,
} from './monthlyBarModel.ts';

const data = {
  months: ['2022-01', '2022-03', '2023-01', '2023-01', '2023-12'],
  cases: [10, 30, 4, 6, 20],
  deaths: [1, 3, 2, 1, 4],
};

test('collectYears returns chronological unique years', () => {
  assert.deepEqual(collectYears(['2023-01', '2021-01', '2023-02']), ['2021', '2023']);
});

test('monthly summaries aggregate duplicate months and preserve missing months', () => {
  const summaries = buildYearSummaries(data, 'cases');
  assert.equal(summaries[0].values.length, MONTH_NAMES.length);
  assert.equal(summaries[0].values[1], null);
  assert.equal(summaries[1].values[0], 10);
  assert.equal(summaries[1].total, null);
  assert.equal(summaries[1].peakMonth, 'Dec');
});

test('metric changes use the same year structure', () => {
  const cases = buildYearSummaries(data, 'cases');
  const deaths = buildYearSummaries(data, 'deaths');
  assert.deepEqual(
    deaths.map((summary) => summary.year),
    cases.map((summary) => summary.year)
  );
  assert.equal(deaths[1].values[0], 3);
});

test('recent year selection keeps the latest chronological years', () => {
  assert.deepEqual(
    getRecentYears(['2019', '2020', '2021', '2022', '2023'], 3),
    ['2021', '2022', '2023']
  );
});

test('annual subtotals are withheld unless all twelve months and coverage are complete', () => {
  const months = Array.from({ length: 12 }, (_, index) => `2024-${String(index + 1).padStart(2, '0')}`);
  const complete = buildYearSummaries({
    months,
    cases: new Array(12).fill(2),
    deaths: new Array(12).fill(0),
    coverage: {
      jurisdiction_universe_size: 4,
      cases_reported_jurisdictions: new Array(12).fill(4),
      deaths_reported_jurisdictions: new Array(12).fill(4),
    },
  }, 'cases')[0];
  assert.equal(complete.total, 24);

  const partial = buildYearSummaries({
    months,
    cases: new Array(12).fill(2),
    deaths: new Array(12).fill(0),
    coverage: {
      jurisdiction_universe_size: 4,
      cases_reported_jurisdictions: [4, ...new Array(11).fill(3)],
      deaths_reported_jurisdictions: new Array(12).fill(4),
    },
  }, 'cases')[0];
  assert.equal(partial.total, null);
});

test('incompatible source definitions stay missing in the monthly view', () => {
  const summary = buildYearSummaries({
    months: ['2024-01'],
    cases: [null],
    deaths: [null],
    coverage: {
      jurisdiction_universe_size: 2,
      cases_reported_jurisdictions: [2],
      deaths_reported_jurisdictions: [2],
      cases_incompatible_months: [true],
      deaths_incompatible_months: [true],
    },
  }, 'cases')[0];
  assert.equal(summary.values[0], null);
  assert.equal(summary.monthlyCoverage[0]?.status, 'incompatible');
  assert.equal(summary.total, null);
});
