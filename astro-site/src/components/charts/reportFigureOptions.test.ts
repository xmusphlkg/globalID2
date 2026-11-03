import test from 'node:test';
import assert from 'node:assert/strict';
import * as echarts from 'echarts';
import { reportFigureView } from '../../../../shared/report-figure-options.ts';

const figure = { id: 'curve', renderer: 'echarts', contract: 'report_v4.figures.1', figure_type: 'epidemic_curve', section_type: 'disease_context', position: 'after_content', data_key: 'curve', title: { en: 'Cases', zh: '病例', fr: 'Cas' }, caption: { en: 'Observed cases' } };
const data = { version: 'report_v4.figures.1', datasets: { curve: { points: [{ period: '2026-01-05', cases: 0 }, { period: '2026-01-12', cases: null }, { period: '2026-01-19', cases: 12 }, { period: '2026-01-26', cases: 15 }] } } };

test('shared preview/public options preserve gaps and real zero, ignore arbitrary options, and render in ECharts', () => {
  const view = reportFigureView({ ...figure, option: { series: [{ data: [999] }] } }, data, 'zh');
  assert.ok(view);
  assert.equal(view.title, '病例');
  assert.deepEqual(view.option.series[0].data, [0, null, 12, 15]);
  assert.ok('connectNulls' in view.option.series[0]);
  assert.equal(view.option.series[0].connectNulls, false);
  assert.equal(view.rows[1][1], '—');
  assert.equal(view.option.tooltip.renderMode, 'richText');
  const chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 760, height: 340 });
  try { chart.setOption(view.option); assert.match(chart.renderToSVGString(), /<svg/); } finally { chart.dispose(); }
});

test('wrong placement, missing dataset and unrecognized types do not render', () => {
  assert.equal(reportFigureView({ ...figure, section_type: 'decision_summary' }, data, 'en'), null);
  assert.equal(reportFigureView({ ...figure, data_key: 'missing' }, data, 'en'), null);
  assert.equal(reportFigureView({ ...figure, figure_type: 'pie' }, data, 'en'), null);
  assert.equal(reportFigureView({ ...figure, position: 'before_content' }, data, 'en'), null);
});

test('attention chart has a fixed score scale and localized disease labels', () => {
  const view = reportFigureView({ ...figure, figure_type: 'attention_ranking_bar', section_type: 'signal_evidence' }, { version: data.version, datasets: { curve: { rows: [{ names: { fr: 'Maladie A' }, score: 70 }, { names: { fr: 'Maladie B' }, score: 30 }] } } }, 'fr');
  assert.ok(view);
  assert.ok('max' in view.option.xAxis);
  assert.equal(view.option.xAxis.max, 100);
  assert.equal(view.rows[0][0], 'Maladie A');
  assert.deepEqual(view.option.series[0].data, [70, 30]);
});

test('expanded library renders real ECharts and exposes statistical tables', () => {
  const points = Array.from({ length: 10 }, (_, i) => ({ period: `2026-01-${String(i + 1).padStart(2, '0')}`, cases: i * 10, mean: i < 2 ? null : (i - 1) * 10 }));
  const datasets: Record<string, unknown> = {
    period_cases_bar: { points }, cumulative_cases_line: { points }, rolling_average_line: { points },
    period_change_bar: { points: points.slice(1).map((p, i) => ({ ...p, cases: i % 2 ? 10 : -5 })) },
    historical_context_line: { points, report_start: '2026-01-08', report_end: '2026-01-10', scope: 'historical_context' },
    case_distribution_boxplot: { summary: [0, 22.5, 45, 67.5, 90], sample_size: 10 },
    case_distribution_histogram: { bins: Array.from({ length: 5 }, (_, i) => ({ lower: i * 20, upper: (i + 1) * 20, count: 2 })), sample_size: 10 },
    attention_dot: { rows: [{ names: { en: 'A' }, score: 60 }, { names: { en: 'B' }, score: 30 }] },
    missingness_bar: { rows: [{ names: { en: 'Cases' }, score: 0 }, { names: { en: 'Deaths' }, score: 100 }] },
  };
  for (const [kind, dataset] of Object.entries(datasets)) {
    const section = kind === 'attention_dot' ? 'signal_evidence' : kind === 'missingness_bar' ? 'data_notes' : 'disease_context';
    const view = reportFigureView({ ...figure, figure_type: kind, section_type: section }, { version: data.version, datasets: { curve: dataset } }, 'en');
    assert.ok(view, kind);
    const chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 390, height: 340 });
    try { chart.setOption(view.option); assert.match(chart.renderToSVGString(), /<svg/, kind); } finally { chart.dispose(); }
    assert.ok(view.rows.length > 0, kind);
    if (kind === 'rolling_average_line') { assert.equal(view.option.series.length, 2); assert.equal(view.rows[0][2], '—'); }
    if (kind === 'period_change_bar') assert.equal(view.option.yAxis.min, -5);
    if (kind === 'historical_context_line') { assert.equal(view.rows[0][2], 'Historical'); assert.equal(view.rows.at(-1)?.[2], 'Report period'); assert.ok(view.option.series[0].markArea); }
  }
});

test('rejects corrupt statistical inputs and wrong quality placement', () => {
  const render = (kind: string, payload: unknown) => reportFigureView({ ...figure, figure_type: kind }, { version: data.version, datasets: { curve: payload } }, 'en');
  assert.equal(render('case_distribution_boxplot', { summary: [0, 30, 20, 40, 50], sample_size: 10 }), null);
  assert.equal(render('case_distribution_histogram', { bins: [], sample_size: 10 }), null);
  assert.equal(render('missingness_bar', { rows: [{ names: { en: 'A' }, score: 10 }, { names: { en: 'B' }, score: 50 }] }), null);
  assert.equal(render('period_cases_bar', { points: [{ period: 'a', cases: 0 }, { period: 'b', cases: Infinity }, { period: 'c', cases: 1 }] }), null);
});

test('history never joins different source/scope cohorts', () => {
  const points = Array.from({ length: 6 }, (_, i) => ({ period: `2026-01-0${i + 1}`, cases: i * 10, source: i < 4 ? 'Old source' : 'New source', segment: i < 4 ? 'old' : 'new' }));
  const view = reportFigureView({ ...figure, figure_type: 'historical_context_line' }, { version: data.version, datasets: { curve: { points, report_start: '2026-01-06', report_end: '2026-01-06', scope: 'historical_context' } } }, 'en');
  assert.ok(view);
  assert.equal(view.option.series.length, 2);
  assert.deepEqual(view.option.series[0].data, [0, 10, 20, 30, null, null]);
  assert.deepEqual(view.option.series[1].data, [null, null, null, null, 40, 50]);
  assert.equal(view.option.series[0].connectNulls, false);
  assert.equal(view.rows[0][3], 'Old source · 1');
  assert.equal(view.rows[5][3], 'New source · 2');
  assert.ok(view.option.series[0].markLine);
});
