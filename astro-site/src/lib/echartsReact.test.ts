import assert from 'node:assert/strict';
import test from 'node:test';

import EChartsReact from './echartsReact.ts';

test('the ECharts React compatibility entry resolves to a component', () => {
  assert.equal(typeof EChartsReact, 'function');
});

test('lean surveillance and heatmap registries render their actual option features in SVG SSR', async () => {
  const { buildSync } = await import('esbuild');
  const { execFileSync } = await import('node:child_process');
  const { resolve } = await import('node:path');
  const cases = [
    { registry: 'echartsSurveillance', option: {
      animation: false, title: { text: 'Surveillance threshold' }, tooltip: { trigger: 'axis' },
      grid: { left: 50, right: 20, bottom: 50 }, xAxis: { type: 'category', data: ['Jan', 'Feb', 'Mar'] }, yAxis: { type: 'value' },
      dataZoom: [{ type: 'slider', start: 0, end: 100 }, { type: 'inside' }],
      series: [{ type: 'line', data: [1, 4, 2], markLine: { label: { formatter: 'Alert threshold' }, data: [{ yAxis: 3 }] } }, { type: 'bar', data: [2, 3, 1] }],
    }, assertions: `assert.match(svg, /Surveillance threshold/); assert.match(svg, /Alert threshold/); assert.equal(chart.getModel().getSeriesByType('line').length, 1); assert.equal(chart.getModel().getSeriesByType('bar').length, 1); assert.equal(chart.getModel().getComponent('dataZoom').get('type'), 'slider');` },
    { registry: 'echartsHeatmap', option: {
      animation: false, grid: { left: 30 }, xAxis: { type: 'category', data: ['Jan', 'Feb'] }, yAxis: { type: 'category', data: ['JP'] },
      visualMap: { show: false, min: 0, max: 10, inRange: { color: ['#e9f4ef', '#0f766e'] } },
      series: [{ type: 'heatmap', data: [[0, 0, 0], [1, 0, 10]] }],
    }, assertions: `assert.equal(chart.getModel().getComponent('visualMap').subType, 'continuous'); assert.ok(svg.includes('fill="rgb(233,244,239)"')); assert.ok(svg.includes('fill="rgb(15,118,110)"')); assert.equal(chart.getModel().getSeriesByType('heatmap').length, 1);` },
  ];
  for (const { registry, option, assertions } of cases) {
    // Separate processes ensure another island cannot silently register a missing feature.
    const source = `import assert from 'node:assert/strict'; import echarts from ${JSON.stringify(resolve('src/lib', registry + '.ts'))}; import { SVGRenderer } from 'echarts/renderers'; echarts.use(SVGRenderer); const chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 640, height: 360 }); chart.setOption(${JSON.stringify(option)}); const svg = chart.renderToSVGString(); assert.match(svg, /<svg/); ${assertions} chart.dispose();`;
    const bundled = buildSync({ stdin: { contents: source, resolveDir: process.cwd(), loader: 'ts' }, bundle: true, platform: 'node', format: 'cjs', write: false, logLevel: 'silent' });
    const output = execFileSync(process.execPath, ['--input-type=commonjs'], { input: bundled.outputFiles[0].text, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'], maxBuffer: 1024 * 1024 });
    assert.equal(output, '', `${registry} renders without warnings`);
  }
});
