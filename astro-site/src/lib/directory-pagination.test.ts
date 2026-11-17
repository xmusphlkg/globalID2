import assert from 'node:assert/strict';
import { test } from 'node:test';
import { directoryPage, DIRECTORY_PAGE_SIZE } from './directory-pagination.ts';

test('a growing directory keeps a bounded page and every result remains reachable in order', () => {
  const rows = Array.from({ length: 10_003 }, (_, id) => ({ id }));
  const all = [];
  for (let page = 1; page <= Math.ceil(rows.length / DIRECTORY_PAGE_SIZE); page++) {
    const window = directoryPage(rows, page);
    assert.ok(window.rows.length <= DIRECTORY_PAGE_SIZE);
    all.push(...window.rows);
  }
  assert.deepEqual(all, rows);
  assert.equal(directoryPage(rows, 1).start, 1);
  assert.equal(directoryPage(rows, 1).end, DIRECTORY_PAGE_SIZE);
});

test('filtering or publication changes clamp a stale page without hiding matching results', () => {
  const filtered = ['JP'];
  assert.deepEqual(directoryPage(filtered, 90), { rows: ['JP'], page: 1, pageCount: 1, total: 1, start: 1, end: 1 });
  assert.equal(directoryPage(filtered, NaN).page, 1);
  assert.equal(directoryPage(filtered, -3).page, 1);
  assert.deepEqual(directoryPage([], 2), { rows: [], page: 1, pageCount: 1, total: 0, start: 0, end: 0 });
});
