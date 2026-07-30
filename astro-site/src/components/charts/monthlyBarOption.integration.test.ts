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
      colors,
    });
    const filteredOption = buildMonthlyBarOption({
      summaries: [createSummary('2026', 20)],
      metricLabel: 'Cases',
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
