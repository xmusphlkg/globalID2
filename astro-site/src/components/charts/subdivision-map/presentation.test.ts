import test from 'node:test';
import assert from 'node:assert/strict';
import { sortRegions, relativeChange } from './presentation.ts';
import type { RegionResult } from './model.ts';

import { prettyBreaks, continuousScale } from './scale.ts';
test('R-style pretty breaks use integer boundaries and cover the maximum', () => {
  for (const maximum of [0, .2, 2.9, 6.57, 30.5, 1000000]) {
    const breaks = prettyBreaks(maximum);
    assert.equal(breaks[0], 0);
    assert.ok(breaks.at(-1)! >= maximum);
    assert.ok(breaks.every(value => Number.isInteger(value)));
    assert.ok(breaks.every((value, index) => index === 0 || value > breaks[index - 1]));
  }
});
test('continuous log scale keeps integer labels at their exact transformed positions', () => {
  const scale = continuousScale(30.5);
  assert.ok(scale.maximum >= 30.5);
  assert.deepEqual(scale.ticks.map(tick => tick.value), [...scale.ticks].sort((a, b) => a.value - b.value).map(tick => tick.value));
  for (const tick of scale.ticks) assert.equal(tick.position, Math.log1p(tick.value) / Math.log1p(scale.maximum));
});
const row = (code: string, value: number | null): RegionResult => ({ region: { code, name_en: code, name_zh: code }, value } as RegionResult);
test('region ranking keeps missing values last, zero sortable, and ties deterministic', () => {
  const rows = [row('Missing', null), row('B', 2), row('A', 2), row('Zero', 0)];
  const previous = new Map<string, RegionResult>();
  assert.deepEqual(sortRegions(rows, previous, { key: 'value', direction: 'desc' }, 'en').map(row => row.region.code), ['A', 'B', 'Zero', 'Missing']);
  assert.deepEqual(sortRegions(rows, previous, { key: 'value', direction: 'asc' }, 'en').map(row => row.region.code), ['Zero', 'A', 'B', 'Missing']);
  assert.equal(rows[0].region.code, 'Missing', 'sorting must not mutate the publication');
});
test('comparison ranking excludes undefined percentage changes and search includes names and codes', () => {
  const rows = [row('CN-BJ', 20), row('CN-AH', 2), row('Zero', 3)];
  rows[0].region.name_zh = '北京市';
  const previous = new Map([row('CN-BJ', 10), row('CN-AH', 4), row('Zero', 0)].map(row => [row.region.code, row]));
  assert.equal(relativeChange(rows[2], previous.get('Zero')), null);
  assert.deepEqual(sortRegions(rows, previous, { key: 'change', direction: 'desc' }, 'zh').map(row => row.region.code), ['CN-BJ', 'CN-AH', 'Zero']);
  assert.deepEqual(sortRegions(rows, previous, { key: 'change', direction: 'asc' }, 'zh').map(row => row.region.code), ['CN-AH', 'CN-BJ', 'Zero']);
  assert.equal(sortRegions(rows, previous, { key: 'name', direction: 'asc' }, 'zh', ' 北京 ')[0].region.code, 'CN-BJ');
  assert.equal(sortRegions(rows, previous, { key: 'name', direction: 'asc' }, 'en', 'cn-bj').length, 1);
});
