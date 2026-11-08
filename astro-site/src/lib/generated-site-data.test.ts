import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { createGeneratedDataReader } from './generated-site-data.ts';

test('development observes new generated data; static render retains its frozen snapshot', () => {
  const root = mkdtempSync(join(tmpdir(), 'gids-generated-reader-'));
  try {
    const file = join(root, 'index.json');
    writeFileSync(file, '{"value":1}');
    const development = createGeneratedDataReader(root, () => false);
    const build = createGeneratedDataReader(root, () => true);
    assert.equal(development('index.json').value, 1);
    const first = build('index.json');
    writeFileSync(file, '{"value":2}');
    assert.equal(development('index.json').value, 2);
    assert.equal(build('index.json'), first);
    assert.equal(createGeneratedDataReader(root, () => true)('index.json').value, 2);
    for (const path of ['../index.json', '/index.json', 'index.txt']) {
      assert.throws(() => development(path), /Invalid generated data path/);
    }
    writeFileSync(file, '[]');
    assert.throws(() => development('index.json'), /Expected generated object/);
    writeFileSync(file, '{broken');
    assert.throws(() => development('index.json'), SyntaxError);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
