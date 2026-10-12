import assert from 'node:assert/strict';
import test from 'node:test';
import * as echarts from 'echarts';
import {
  MONTHLY_BAR_REPLACE_MERGE,
  buildMonthlyBarOption,
} from './monthlyBarOption.ts';
import type { YearSummary } from './monthlyBarModel.ts';

const colors = {
  font: '#556070',
  line: '#c9c2b8',
  grid: '#d9d2c7',
  hoverBg: '#fffdfa',
  hoverBorder: '#c9c2b8',
  hoverFont: '#162232',
  title: '#162232',
};

function createSummary(year: string, value: number): YearSummary {
  return {
    year,
    values: new Array<number>(12).fill(value),
    total: value * 12,
    monthlyCoverage: new Array(12).fill(null),
    peakMonth: 'Jan',
    peakValue: value,
    color: '#0072b2',
  };
}

test('monthly chart option and update contract are accepted by ECharts', () => {
  const chart = echarts.init(null, null, {
    renderer: 'svg',
    ssr: true,
    width: 800,
    height: 400,
  });

  try {
    const initialOption = buildMonthlyBarOption({
      summaries: [createSummary('2025', 10), createSummary('2026', 20)],
      metricLabel: 'Cases',
      lang: 'en',
      colors,
    });
    const filteredOption = buildMonthlyBarOption({
      summaries: [createSummary('2026', 20)],
      metricLabel: 'Cases',
      lang: 'en',
      colors,
    });

    assert.doesNotThrow(() => {
      chart.setOption(initialOption, { replaceMerge: MONTHLY_BAR_REPLACE_MERGE });
      chart.setOption(filteredOption, { replaceMerge: MONTHLY_BAR_REPLACE_MERGE });
    });
    assert.deepEqual(MONTHLY_BAR_REPLACE_MERGE, ['series']);
    assert.match(chart.renderToSVGString(), /^<svg/);
  } finally {
    chart.dispose();
  }
});

test('monthly tooltip distinguishes incompatible missing values from zero', () => {
  const summary = createSummary('2024', 0);
  summary.values[0] = null;
  summary.monthlyCoverage[0] = { reported: 2, eligible: 2, status: 'incompatible' };
  const option = buildMonthlyBarOption({
    summaries: [summary], metricLabel: 'Cases', lang: 'en', colors,
  });
  const tooltip = option.tooltip.formatter([
    { axisValueLabel: 'Jan', dataIndex: 0, seriesName: '2024', value: null, marker: '' },
  ]);
  assert.match(tooltip, /2024: <b>—<\/b>/);
  assert.match(tooltip, /incompatible definitions/);
});
