import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, writeFileSync, readFileSync, rmSync, statSync, utimesSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createJsonFileCache } from './json-file-cache.ts';

test('parsed JSON is reused, same-size edits with restored mtime and deletion are detected', () => {
  const dir = mkdtempSync(join(tmpdir(), 'json-cache-'));
  try {
    const file = join(dir, 'data.json'); let reads = 0;
    const cache = createJsonFileCache({ now: () => Date.now() + 2000,
      read: path => { reads++; return readFileSync(path, 'utf8'); } });
    writeFileSync(file, '{"value":1}');
    const first = cache.read(file); assert.equal(cache.read(file), first); assert.equal(reads, 1);
    const stat = statSync(file);
    writeFileSync(file, '{"value":2}'); utimesSync(file, stat.atime, stat.mtime);
    assert.deepEqual(cache.read(file), { value: 2 }); assert.equal(reads, 2);
    rmSync(file); assert.throws(() => cache.read(file));
    writeFileSync(file, 'invalid'); assert.throws(() => cache.read(file));
    writeFileSync(file, '{"value":3}'); assert.deepEqual(cache.read(file), { value: 3 });
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('input-byte budget bounds retention, oversize files bypass it, and racing reads fail', () => {
  const dir = mkdtempSync(join(tmpdir(), 'json-cache-budget-'));
  try {
    let reads = 0;
    const cache = createJsonFileCache({ maxBytes: 16, maxFileBytes: 12, now: () => Date.now() + 2000,
      read: path => { reads++; return readFileSync(path, 'utf8'); } });
    const a = join(dir, 'a.json'); const b = join(dir, 'b.json'); const big = join(dir, 'big.json');
    writeFileSync(a, '{"x":123}'); writeFileSync(b, '{"x":456}'); writeFileSync(big, JSON.stringify('x'.repeat(40)));
    cache.read(a); cache.read(b); cache.read(a);
    assert.equal(reads, 3); assert.equal(cache.size, 1); assert.ok(cache.bytes <= 16);
    cache.read(big); cache.read(big); assert.equal(reads, 5); assert.equal(cache.size, 1);
    const racing = createJsonFileCache({ read: path => { const body = readFileSync(path, 'utf8'); writeFileSync(path, '{"x":123456}'); return body; } });
    assert.throws(() => racing.read(a), /changed while reading/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
