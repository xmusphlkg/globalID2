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

test('monthly summaries aggregate duplicate months and fill missing months', () => {
  const summaries = buildYearSummaries(data, 'cases');
  assert.equal(summaries[0].values.length, MONTH_NAMES.length);
  assert.equal(summaries[0].values[1], 0);
  assert.equal(summaries[1].values[0], 10);
  assert.equal(summaries[1].total, 30);
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
