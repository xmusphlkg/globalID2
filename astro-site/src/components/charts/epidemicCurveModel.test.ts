import assert from 'node:assert/strict';
import test from 'node:test';
import {
  INITIAL_CURVE_VIEW_STATE,
  dateWindowFromZoom,
  epidemicCurveViewReducer,
  reconcileDateWindow,
} from './epidemicCurveModel.ts';

const dates = [
  '2020-01-01',
  '2020-02-01',
  '2020-03-01',
  '2020-04-01',
  '2020-05-01',
];

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
