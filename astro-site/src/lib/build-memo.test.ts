import assert from 'node:assert/strict';
import { test } from 'node:test';
import { memoizeForBuild } from './build-memo.ts';

test('one static snapshot is shared, dev reads stay fresh, and errors are never cached', () => {
  let build = false; let calls = 0;
  const read = memoizeForBuild(() => ++calls, () => build);
  assert.equal(read(), 1); assert.equal(read(), 2);
  build = true;
  assert.equal(read(), 3); assert.equal(read(), 3);
  build = false; assert.equal(read(), 4);
  build = true; assert.equal(read(), 5);
  let fail = true;
  const retry = memoizeForBuild(() => { if (fail) throw new Error('not ready'); return 10; }, () => true);
  assert.throws(retry, /not ready/); fail = false; assert.equal(retry(), 10);
});
