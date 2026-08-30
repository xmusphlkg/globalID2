import assert from 'node:assert/strict';
import test from 'node:test';
import {
  DEFAULT_METRIC,
  INITIAL_CURVE_VIEW_STATE,
  buildStableSeriesColorMap,
  assessComparison,
  buildHistoricalReference,
  calculateIncidenceFromReferencePopulation,
  dateWindowFromZoom,
  epidemicCurveViewReducer,
  formatIncidenceMetricLabel,
  getCurveLineSampling,
  getEffectiveProvisionalFrom,
  getOutbreakEligibility,
  getSelectableSourceSeries,
  getSeriesGranularity,
  hasMixedSourceGranularities,
  hasPublicProjection,
  insertMissingPeriodBreaks,
  normalizeTrendIndex,
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

test('trend index compares shape without inventing weekly counts', () => {
  assert.deepEqual(normalizeTrendIndex([10, 20, null, 5]), [50, 100, null, 25]);
  assert.deepEqual(normalizeTrendIndex([0, 0]), [0, 0]);
});

test('historical reference uses prior matching seasons without future leakage', () => {
  const reference = buildHistoricalReference(
    [
      '2020-01-01',
      '2021-01-01',
      '2022-01-01',
      '2023-01-01',
      '2024-01-01',
      '2025-01-01',
    ],
    [1, 2, 3, 4, 5, 6],
    'monthly'
  );

  assert.deepEqual(reference.expected, [null, null, null, null, null, 3]);
  assert.deepEqual(reference.lower, [null, null, null, null, null, 2]);
  assert.deepEqual(reference.upper, [null, null, null, null, null, 4]);
  assert.deepEqual(reference.index, [null, null, null, null, null, 200]);
  assert.equal(reference.eligiblePointCount, 1);
});

test('missing reporting periods break lines instead of becoming implicit zeroes', () => {
  const broken = insertMissingPeriodBreaks(
    ['2024-01-01', '2024-03-01'],
    [5, 7],
    'monthly'
  );

  assert.equal(broken.dates.length, 3);
  assert.deepEqual(broken.values, [5, null, 7]);
  assert.equal(getCurveLineSampling(broken.values, 'monitor'), undefined);
});

test('explicit missing values disable line sampling so ECharts preserves gaps', () => {
  assert.equal(getCurveLineSampling([5, null, 7], 'monitor'), undefined);
  assert.equal(getCurveLineSampling([5, Number.NaN, 7], 'monitor'), undefined);
  assert.equal(getCurveLineSampling([5, 6, 7], 'monitor'), 'lttb');
  assert.equal(getCurveLineSampling([5, 6, 7], 'outbreak'), undefined);
});

test('point granularities break mixed-cadence public curves conservatively', () => {
  assert.deepEqual(
    insertMissingPeriodBreaks(
      ['2024-01-07', '2024-02-01'],
      [5, 7],
      'mixed',
      ['weekly', 'monthly'],
    ).values,
    [5, null, 7],
  );
  assert.deepEqual(
    insertMissingPeriodBreaks(
      ['2024-01-07', '2024-01-28'],
      [5, 7],
      'mixed',
      ['weekly', 'weekly'],
    ).values,
    [5, null, 7],
  );
  assert.deepEqual(
    insertMissingPeriodBreaks(
      ['2024-01-07', '2024-02-01'],
      [5, 7],
      'unknown',
      [null, null],
    ).values,
    [5, 7],
  );
});

test('incidence labels retain the source-period denominator', () => {
  assert.equal(
    formatIncidenceMetricLabel(['weekly'], 'en'),
    'Cases per 100k per week'
  );
  assert.equal(
    formatIncidenceMetricLabel(['weekly', 'annual'], 'en'),
    'Cases per 100k per respective source period'
  );
});

test('selected source cases retain incidence calculation using reference population', () => {
  const reference = {
    ...series('monthly'),
    dates: ['2023-01-01', '2024-01-01'],
    cases: [1_000, 2_000],
    incidence_rates: [1, 2],
  };
  assert.deepEqual(
    calculateIncidenceFromReferencePopulation(
      reference,
      ['2024-02-01', '2024-03-01'],
      [500, 0]
    ),
    [0.5, 0]
  );
});

test('comparison blocks explicitly non-comparable sources and enforces overlap', () => {
  const left = {
    ...series('monthly'),
    dates: ['2024-01-01', '2024-02-01'],
    source_series: [{
      series_code: 'left',
      comparability: 'direct',
      metric_type: 'case_notifications',
      reporting_basis: 'notification',
      time_basis: 'report date',
      definition_version: '1',
    }],
  };
  const right = {
    ...series('monthly'),
    dates: ['2024-02-01', '2024-03-01'],
    source_series: [{
      series_code: 'right',
      comparability: 'not_comparable',
      metric_type: 'case_notifications',
      reporting_basis: 'notification',
      time_basis: 'report date',
      definition_version: '1',
    }],
  };

  const assessment = assessComparison([left, right]);
  assert.equal(assessment.level, 'blocked');
  assert.ok(assessment.reasons.includes('source_not_comparable'));
  assert.deepEqual(assessment.commonWindow, {
    startDate: '2024-02-01',
    endDate: '2024-02-01',
  });
});

test('outbreak mode requires fine-grained onset-time observations', () => {
  const eligible = {
    ...series('weekly', [10, 20]),
    source_series: [{
      series_code: 'onset-weekly',
      temporal_granularity: 'weekly',
      time_basis: 'symptom onset date',
    }],
  };
  assert.equal(getOutbreakEligibility(eligible).eligible, true);
  assert.equal(getOutbreakEligibility(series('annual')).eligible, false);
});

test('series colors are keyed by identity rather than current selection order', () => {
  const colors = ['blue', 'orange', 'green'];
  const complete = buildStableSeriesColorMap(['US', 'BR', 'JP'], colors);
  const reordered = buildStableSeriesColorMap(['JP', 'US', 'BR'], colors);

  assert.equal(complete.get('BR'), reordered.get('BR'));
  assert.equal(complete.get('JP'), reordered.get('JP'));
  assert.equal(complete.get('US'), reordered.get('US'));
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

test('a selected native weekly source restores the weekly reported cases view', () => {
  const value = {
    ...series('monthly'),
    source_series: [
      {
        series_code: 'weekly-current',
        temporal_granularity: 'weekly',
        dates: ['2024-01-07', '2024-01-14'],
        values: [4, 7],
      },
    ],
  };

  const selected = selectSourceSeries(value, 'weekly-current');
  assert.deepEqual(selected.weekly_equiv_cases, [4, 7]);
  assert.equal(supportsWeeklyEquivalent(selected), true);
});

test('a selected source preserves missing values and provisional metadata', () => {
  const value = {
    ...series('monthly'),
    source_series: [{
      series_code: 'weekly-provisional',
      temporal_granularity: 'weekly',
      dates: ['2024-01-07', '2024-01-14'],
      values: [4, null],
      provisional_from: '2024-01-14',
    }],
  };

  const selected = selectSourceSeries(value, 'weekly-provisional');
  assert.deepEqual(selected.cases, [4, null]);
  assert.deepEqual(selected.weekly_equiv_cases, [4, null]);
  assert.equal(selected.total_cases, 4);
  assert.equal(selected.provisional_from, '2024-01-14');
});

test('raw point metadata never expands a provisional boundary', () => {
  const value = {
    ...series('monthly'),
    provisional_from: '2024-01-01',
    selected_series_codes: ['monthly-current'],
    source_series: [{
      series_code: 'monthly-current',
      temporal_granularity: 'monthly',
      dates: ['2024-01-01', '2024-02-01', '2024-03-01'],
      values: [4, 5, 6],
      point_quality_statuses: ['raw', 'raw', 'provisional'],
      provisional_from: '2024-01-01',
    }],
  };

  assert.equal(getEffectiveProvisionalFrom(value), '2024-03-01');
  assert.equal(
    selectSourceSeries(value, 'monthly-current').provisional_from,
    '2024-03-01',
  );
});

test('an all-raw source does not inherit a stale provisional marker', () => {
  const value = {
    ...series('monthly'),
    provisional_from: '2024-01-01',
    selected_series_codes: ['monthly-current'],
    source_series: [{
      series_code: 'monthly-current',
      temporal_granularity: 'monthly',
      dates: ['2024-01-01', '2024-02-01'],
      values: [4, 5],
      point_quality_statuses: ['raw', 'raw'],
      provisional_from: '2024-01-01',
    }],
  };

  assert.equal(getEffectiveProvisionalFrom(value), null);
  assert.equal(selectSourceSeries(value, 'monthly-current').provisional_from, null);
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
