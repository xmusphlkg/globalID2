import assert from 'node:assert/strict';
import test from 'node:test';
import {
  DEFAULT_METRIC,
  INITIAL_CURVE_VIEW_STATE,
  dateWindowFromZoom,
  epidemicCurveViewReducer,
  getSelectableSourceSeries,
  getSeriesGranularity,
  hasMixedSourceGranularities,
  hasPublicProjection,
  reconcileDateWindow,
  selectSourceSeries,
  supportsWeeklyEquivalent,
} from './epidemicCurveModel.ts';

const dates = [
  '2020-01-01',
  '2020-02-01',
  '2020-03-01',
  '2020-04-01',
  '2020-05-01',
];

const series = (granularity: string, weeklyValues: number[] = []) => ({
  disease_id: 'D_TEST',
  name_en: 'Test disease',
  name_zh: '测试疾病',
  dates: ['2024-01-01', '2024-02-01'],
  cases: [10, 20],
  weekly_equiv_cases: weeklyValues,
  deaths: [0, 0],
  incidence_rates: [null, null],
  total_cases: 30,
  period_granularity: granularity,
});

test('source-period cases are the safe default metric', () => {
  assert.equal(DEFAULT_METRIC, 'cases');
  assert.equal(INITIAL_CURVE_VIEW_STATE.metric, 'cases');
});

test('weekly values are exposed only for explicitly weekly series', () => {
  assert.equal(supportsWeeklyEquivalent(series('monthly', [10, 20])), false);
  assert.equal(supportsWeeklyEquivalent(series('annual', [10, 20])), false);
  assert.equal(supportsWeeklyEquivalent(series('weekly', [10, 20])), true);
});

test('selected source metadata supplies a missing display granularity', () => {
  const value = {
    ...series(''),
    selected_series_codes: ['respiratory:flu:weekly-diagnoses'],
    source_series: [
      {
        series_code: 'respiratory:flu:weekly-diagnoses',
        temporal_granularity: 'weekly',
      },
    ],
  };

  assert.equal(getSeriesGranularity(value), 'weekly');
});

test('a concrete source selection uses its original dates, values, and grain', () => {
  const value = {
    ...series('monthly'),
    selected_series_codes: ['projection-monthly'],
    source_series: [
      {
        series_code: 'projection-monthly',
        source_label: 'Monthly registry',
        temporal_granularity: 'monthly',
        reporting_basis: 'registry_diagnoses',
        availability_status: 'historical',
        dates: ['1997-01-01', '1997-02-01'],
        values: [4, 7],
        total_value: 11,
      },
      {
        series_code: 'annual-current',
        source_label: 'Annual dashboard',
        temporal_granularity: 'annual',
        reporting_basis: 'national_notifications',
        availability_status: 'active',
        dates: ['2023-01-01', '2024-01-01'],
        values: [12, 15],
        total_value: 27,
      },
    ],
  };

  const selected = selectSourceSeries(value, 'annual-current');
  assert.deepEqual(selected.dates, ['2023-01-01', '2024-01-01']);
  assert.deepEqual(selected.cases, [12, 15]);
  assert.equal(selected.total_cases, 27);
  assert.equal(selected.period_granularity, 'annual');
  assert.deepEqual(selected.selected_series_codes, ['annual-current']);
  assert.equal(hasMixedSourceGranularities(value), true);
  assert.equal(getSelectableSourceSeries(value).length, 2);
});

test('source-only entries are not mistaken for a public projection', () => {
  const sourceOnly = {
    ...series(''),
    dates: [],
    cases: [],
    source_series: [
      {
        series_code: 'history-only',
        temporal_granularity: 'annual',
        dates: ['1997-01-01'],
        values: [3],
      },
    ],
  };

  assert.equal(hasPublicProjection(sourceOnly), false);
  assert.deepEqual(selectSourceSeries(sourceOnly, null).dates, []);
  assert.deepEqual(selectSourceSeries(sourceOnly, 'history-only').cases, [3]);
});

test('metric changes preserve the semantic date window', () => {
  const withDateWindow = epidemicCurveViewReducer(INITIAL_CURVE_VIEW_STATE, {
    type: 'dateWindowChanged',
    dateWindow: {
      startDate: '2020-02-01',
      endDate: '2020-04-01',
    },
  });

  const deaths = epidemicCurveViewReducer(withDateWindow, {
    type: 'metricChanged',
    metric: 'deaths',
  });
  const incidence = epidemicCurveViewReducer(deaths, {
    type: 'metricChanged',
    metric: 'incidence_rates',
  });

  assert.deepEqual(incidence.dateWindow, {
    startDate: '2020-02-01',
    endDate: '2020-04-01',
  });
});

test('data zoom resolves to the same date window used by every metric', () => {
  assert.deepEqual(dateWindowFromZoom(dates, 25, 75), {
    startDate: '2020-02-01',
    endDate: '2020-04-01',
  });
});

test('data zoom prefers real axis boundaries for irregular reporting dates', () => {
  const irregularDates = [
    '2020-01-01',
    '2020-01-02',
    '2020-08-01',
    '2020-12-31',
  ];

  assert.deepEqual(
    dateWindowFromZoom(
      irregularDates,
      40,
      90,
      Date.parse('2020-06-01'),
      Date.parse('2020-10-01')
    ),
    {
      startDate: '2020-08-01',
      endDate: '2020-08-01',
    }
  );
});

test('date windows are clamped safely when the selected entity domain changes', () => {
  assert.deepEqual(
    reconcileDateWindow(
      { startDate: '2019-01-01', endDate: '2021-01-01' },
      dates
    ),
    { startDate: '2020-01-01', endDate: '2020-05-01' }
  );
});
