import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { readResearchPublicFields } from './research-public-source.ts';

test('reads only requested fields and reconstructs list, object and value shards', () => {
  const root = mkdtempSync(resolve(tmpdir(), 'gids-research-source-'));
  try {
    mkdirSync(resolve(root, 'shards'));
    writeFileSync(resolve(root, 'index.json'), JSON.stringify({
      metrics: { count: 2 }, articles: { sharded: true, kind: 'list', chunks: [{ file: 'shards/a.json' }] },
      graph: { sharded: true, kind: 'object', parts: { nodes: { sharded: true, kind: 'value', file: 'shards/b.json' } } },
      unused: { sharded: true, kind: 'value', file: 'shards/missing.json' },
    }));
    writeFileSync(resolve(root, 'shards/a.json'), '{"records":[{"id":1},{"id":2}]}');
    writeFileSync(resolve(root, 'shards/b.json'), '{"value":["node"]}');
    assert.deepEqual(readResearchPublicFields(root, ['metrics', 'articles', 'graph']), {
      metrics: { count: 2 }, articles: [{ id: 1 }, { id: 2 }], graph: { nodes: ['node'] },
    });
    assert.throws(() => readResearchPublicFields(root), /ENOENT/);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
