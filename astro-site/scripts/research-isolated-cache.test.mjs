import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { articlePaths } from './research-build-cache.mjs';
import { planResearchArticles, readResearchCache, restoreResearchArticles, saveResearchCache } from './research-isolated-cache.mjs';

test('only changed articles render again and old assets remain available', () => {
  const root = mkdtempSync(resolve(tmpdir(), 'gids-research-cache-'));
  try {
    const output = resolve(root, 'first'), cache = resolve(root, 'cache'), second = resolve(root, 'second');
    const inputs = { schema: 1, shared: 'template-1', articles: {
      alpha: { digest: 'a', indexable: true }, beta: { digest: 'b', indexable: true },
    } };
    for (const slug of Object.keys(inputs.articles)) for (const path of articlePaths(slug, true)) {
      const file = resolve(output, path); mkdirSync(dirname(file), { recursive: true }); writeFileSync(file, slug);
    }
    mkdirSync(resolve(output, '_astro'), { recursive: true });
    writeFileSync(resolve(output, '_astro/old.css'), 'old');
    saveResearchCache({ cache, inputs, output });
    writeFileSync(resolve(output, articlePaths('alpha', true)[0]), 'changed outside cache');
    const previous = readResearchCache(cache);
    const next = { ...inputs, articles: { ...inputs.articles, beta: { digest: 'changed', indexable: true } } };
    const plan = planResearchArticles(next, previous.manifest);
    assert.deepEqual(plan, { selected: ['beta'], reusable: ['alpha'] });
    restoreResearchArticles({ previous, inputs: next, reusable: plan.reusable, output: second });
    assert.equal(readFileSync(resolve(second, articlePaths('alpha', true)[0]), 'utf8'), 'alpha');
    assert.equal(readFileSync(resolve(second, '_astro/old.css'), 'utf8'), 'old');
    assert.deepEqual(planResearchArticles({ ...next, shared: 'new template' }, previous.manifest),
      { selected: ['alpha', 'beta'], reusable: [] });
  } finally { rmSync(root, { recursive: true, force: true }); }
});
