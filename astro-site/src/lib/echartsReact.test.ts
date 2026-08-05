import assert from 'node:assert/strict';
import test from 'node:test';

import EChartsReact from './echartsReact.ts';

test('the ECharts React compatibility entry resolves to a component', () => {
  assert.equal(typeof EChartsReact, 'function');
});
