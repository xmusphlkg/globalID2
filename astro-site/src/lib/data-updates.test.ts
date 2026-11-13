import assert from 'node:assert/strict';
import test from 'node:test';
import { dailyCounts, daysCovering, monthEnd, monthsCovering, rangeProblem } from './data-updates.ts';

test('month end and covering months use UTC calendar boundaries', () => {
  assert.equal(monthEnd('2026-02'), '2026-02-28');
  assert.equal(monthEnd('2024-02'), '2024-02-29');
  assert.deepEqual(monthsCovering('2026-10-15', '2026-12-01'), ['2026-10', '2026-11', '2026-12']);
  assert.deepEqual(monthsCovering('2026-11-02', '2026-10-01'), []);
});

test('daily counts keep zero days inside the selected range', () => {
  const days = daysCovering('2026-11-06', '2026-11-08');
  assert.deepEqual(dailyCounts(days, ['2026-11-07T12:00:00Z', '2026-11-07T18:00:00Z', '2026-10-01']), [
    { day: '2026-11-06', count: 0 },
    { day: '2026-11-07', count: 2 },
    { day: '2026-11-08', count: 0 },
  ]);
});

test('range problems reject a reversed or very long selection', () => {
  assert.equal(rangeProblem('2026-11-08', '2026-11-01'), 'reversed');
  assert.equal(rangeProblem('2024-01-01', '2026-11-01'), 'wide');
  assert.equal(rangeProblem('2026-11-01', '2026-11-30'), null);
});
