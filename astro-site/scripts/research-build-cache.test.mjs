import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { articlePaths, planResearchBuild } from './research-build-cache.mjs';

const hash = value => createHash('sha256').update(value).digest('hex');

test('selects only changed article bundles and rebuilds when shared inputs change', () => {
  const root = mkdtempSync(join(tmpdir(), 'gids-research-cache-'));
  try {
    const articles = {};
    for (const slug of ['alpha', 'beta']) {
      const paths = articlePaths(slug, true);
      const files = {};
      for (const path of paths) {
        const target = join(root, path);
        mkdirSync(dirname(target), { recursive: true });
        writeFileSync(target, `${slug}:${path}`);
        files[path] = hash(readFileSync(target));
      }
      articles[slug] = { fingerprint: slug, paths, files };
    }
    const previous = { schema: 1, shared: 'same', articles };
    const snapshot = { schema: 1, shared: 'same', articles: {
      alpha: { fingerprint: 'alpha', paths: articlePaths('alpha', true) },
      beta: { fingerprint: 'new-beta', paths: articlePaths('beta', true) },
    } };
    assert.deepEqual(planResearchBuild({ incremental: true, snapshot, previous, outputRoot: root }).selected, ['beta']);
    assert.deepEqual(planResearchBuild({ incremental: true, snapshot: { ...snapshot, shared: 'new' }, previous, outputRoot: root }).selected, ['alpha', 'beta']);
    rmSync(join(root, articlePaths('alpha', true)[0]));
    assert.deepEqual(planResearchBuild({ incremental: true, snapshot, previous, outputRoot: root }).selected, ['alpha', 'beta']);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
