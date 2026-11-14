import assert from 'node:assert/strict';
import test from 'node:test';
import { dailyCounts, daysCovering, monthEnd, monthsCovering, rangeProblem, utcDay } from './data-updates.ts';

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
  assert.equal(rangeProblem('2025-01-01', '2026-12-31'), null);
  assert.equal(rangeProblem('2025-01-01', '2027-01-01'), 'wide');
});

test('UTC days and counts agree across midnight and month boundaries', () => {
  assert.equal(utcDay('2026-12-01T00:30:00+08:00'), '2026-11-30');
  assert.equal(utcDay('2026-10-31T23:30:00-03:00'), '2026-11-01');
  assert.equal(utcDay('2026-11-30'), '2026-11-30');
  assert.deepEqual(dailyCounts(['2026-11-01', '2026-11-30'], [
    '2026-12-01T00:30:00+08:00', '2026-10-31T23:30:00-03:00', '2026-11-30', 'invalid', '2026-11-31T00:00:00Z',
  ]), [{ day: '2026-11-01', count: 1 }, { day: '2026-11-30', count: 2 }]);
});

test('incomplete and impossible calendar dates do not create a false empty range', () => {
  assert.equal(rangeProblem('', '2026-11-30'), 'incomplete');
  assert.equal(rangeProblem('2026-11-01', ''), 'incomplete');
  for (const date of ['2026-02-29', '2026-11-31', '2026-13-01', 'not-a-day']) {
    assert.equal(rangeProblem(date, '2026-12-31'), 'invalid');
    assert.deepEqual(monthsCovering(date, '2026-12-31'), []);
    assert.deepEqual(daysCovering(date, '2026-12-31'), []);
    assert.equal(utcDay(date), null);
  }
  assert.deepEqual(daysCovering('2024-02-28', '2024-03-01'), ['2024-02-28', '2024-02-29', '2024-03-01']);
});
