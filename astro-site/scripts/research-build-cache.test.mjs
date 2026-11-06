import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { articlePaths, finalizeResearchSnapshot, mergeAstroAssetTree, planResearchBuild, replaceAstroAssetCache, reusedAssetReferencesAvailable, summarizeBuildPlan } from './research-build-cache.mjs';

const hash = value => createHash('sha256').update(value).digest('hex');

test('incremental finalization preserves unchanged cache files and repairs missing or changed bundles', t => {
  const root = mkdtempSync(join(tmpdir(), 'gids-finalize-reuse-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const outputRoot = join(root, 'dist');
  const pageCache = join(root, 'pages');
  const targetManifest = join(root, 'manifest.json');
  mkdirSync(outputRoot);
  writeFileSync(join(outputRoot, 'index.html'), '<main>one</main>');
  const snapshot = { schema: 1, shared: 'one', articles: { page: { fingerprint: 'one', paths: ['index.html'] } } };
  const options = { outputRoot, pageCache, targetManifest, sourcePending: join(root, 'pending.json') };
  const finalize = reused => finalizeResearchSnapshot({ ...structuredClone(snapshot), reusedKeys: reused }, options);
  finalize([]);
  const cached = join(pageCache, 'index.html');
  const original = statSync(cached, { bigint: true });
  finalize(['page']);
  assert.equal(statSync(cached, { bigint: true }).ctimeNs, original.ctimeNs);
  rmSync(cached);
  finalize(['page']);
  assert.equal(readFileSync(cached, 'utf8'), '<main>one</main>');
  writeFileSync(join(outputRoot, 'index.html'), '<main>two</main>');
  finalize(['page']);
  assert.equal(readFileSync(cached, 'utf8'), '<main>two</main>');
  assert.equal(JSON.parse(readFileSync(targetManifest)).articles.page.files['index.html'], hash('<main>two</main>'));
});

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
    assert.deepEqual(planResearchBuild({ incremental: true, snapshot, previous, outputRoot: root }).reasons,
      { alpha: 'reused', beta: 'inputs' });
    assert.deepEqual(planResearchBuild({ incremental: true, snapshot: { ...snapshot, shared: 'new' }, previous, outputRoot: root }).selected, ['alpha', 'beta']);
    rmSync(join(root, articlePaths('alpha', true)[0]));
    assert.deepEqual(planResearchBuild({ incremental: true, snapshot, previous, outputRoot: root }).selected, ['alpha', 'beta']);
    assert.equal(planResearchBuild({ incremental: true, snapshot, previous, outputRoot: root }).reasons.alpha, 'missing-output');
    writeFileSync(join(root, articlePaths('alpha', true)[0]), 'damaged');
    assert.equal(planResearchBuild({ incremental: true, snapshot, previous, outputRoot: root }).reasons.alpha, 'damaged-output');
    const removed = planResearchBuild({ incremental: true, snapshot: { ...snapshot, articles: {} }, previous, outputRoot: root });
    assert.deepEqual(removed.removed, ['alpha', 'beta']);
    assert.equal(summarizeBuildPlan(removed).removed, 2);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('finalizes the exact rendered snapshot after source data advances', () => {
  const root = mkdtempSync(join(tmpdir(), 'gids-research-finalize-'));
  try {
    const paths = articlePaths('alpha', false);
    for (const path of paths) {
      const target = join(root, 'dist', path);
      mkdirSync(dirname(target), { recursive: true });
      writeFileSync(target, `rendered:${path}`);
    }
    const snapshot = {
      schema: 1,
      shared: 'rendered-source',
      articles: { alpha: { fingerprint: 'rendered-article', paths } },
    };
    const manifest = join(root, 'cache', 'manifest.json');
    const pending = join(root, 'cache', 'pending.json');
    mkdirSync(dirname(pending), { recursive: true });
    writeFileSync(pending, JSON.stringify(snapshot));

    // The caller's current sources can have advanced; the built output and
    // pending snapshot still form the cache entry to record.
    const changedSource = { ...snapshot, shared: 'newer-source' };
    assert.notDeepEqual(changedSource, snapshot);
    finalizeResearchSnapshot(snapshot, {
      outputRoot: join(root, 'dist'),
      targetManifest: manifest,
      sourcePending: pending,
      pageCache: join(root, 'page-cache'),
    });

    const recorded = JSON.parse(readFileSync(manifest, 'utf8'));
    assert.equal(recorded.shared, 'rendered-source');
    assert.deepEqual(Object.keys(recorded.articles.alpha.files), paths);
    assert.equal(existsSync(pending), false);
    // The rendered bundle survives for the next incremental build even after
    // the release step moves it out of dist.
    for (const path of paths) assert.equal(readFileSync(join(root, 'page-cache', path), 'utf8'), `rendered:${path}`);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('restores only missing shared assets and refreshes the fingerprinted asset cache', () => {
  const root = mkdtempSync(join(tmpdir(), 'gids-astro-assets-'));
  try {
    const cache = join(root, 'cache');
    const output = join(root, 'dist', '_astro');
    mkdirSync(join(cache, 'chunks'), { recursive: true });
    mkdirSync(join(output, 'chunks'), { recursive: true });
    writeFileSync(join(cache, 'chunks', 'report.abc.js'), 'cached asset');
    writeFileSync(join(output, 'chunks', 'report.abc.js'), 'fresh asset');

    assert.equal(mergeAstroAssetTree(cache, output), 0);
    assert.equal(readFileSync(join(output, 'chunks', 'report.abc.js'), 'utf8'), 'fresh asset');

    writeFileSync(join(cache, 'chunks', 'chart.def.js'), 'cached chart');
    assert.equal(mergeAstroAssetTree(cache, output), 1);
    assert.equal(readFileSync(join(output, 'chunks', 'chart.def.js'), 'utf8'), 'cached chart');

    const refreshed = join(root, 'refreshed-cache');
    assert.equal(replaceAstroAssetCache(output, refreshed), 2);
    assert.equal(readFileSync(join(refreshed, 'chunks', 'report.abc.js'), 'utf8'), 'fresh asset');
    assert.equal(readFileSync(join(refreshed, 'chunks', 'chart.def.js'), 'utf8'), 'cached chart');
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('reused HTML is rejected when a referenced client asset is missing', () => {
  const root = mkdtempSync(join(tmpdir(), 'gids-missing-asset-'));
  try {
    writeFileSync(join(root, 'index.html'), '<script src="/_astro/client.abc.js"></script>');
    const plan = { reused: ['page'], snapshot: { articles: { page: { paths: ['index.html'] } } } };
    assert.equal(reusedAssetReferencesAvailable(plan, root), false);
    mkdirSync(join(root, '_astro'));
    writeFileSync(join(root, '_astro/client.abc.js'), 'export {};');
    assert.equal(reusedAssetReferencesAvailable(plan, root), true);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('finalized asset references survive reuse and still reject missing assets', () => {
  const root = mkdtempSync(join(tmpdir(), 'gids-cached-asset-references-'));
  try {
    const outputRoot = join(root, 'dist');
    const pageCache = join(root, 'pages');
    const targetManifest = join(root, 'manifest.json');
    mkdirSync(join(outputRoot, '_astro'), { recursive: true });
    writeFileSync(join(outputRoot, 'index.html'), '<script src="/_astro/client.js"></script>');
    writeFileSync(join(outputRoot, '_astro/client.js'), 'export {};');
    const snapshot = { schema: 1, shared: 'one', articles: { page: { fingerprint: 'one', paths: ['index.html'] } } };
    finalizeResearchSnapshot(structuredClone(snapshot), {
      outputRoot, pageCache, targetManifest, sourcePending: join(root, 'pending.json'),
    });
    const plan = planResearchBuild({ incremental: true, snapshot, outputRoot: pageCache,
      previous: JSON.parse(readFileSync(targetManifest, 'utf8')) });
    assert.deepEqual(plan.reused, ['page']);
    assert.deepEqual(plan.assetReferences.page['index.html'], ['client.js']);
    assert.equal(reusedAssetReferencesAvailable(plan, outputRoot), true);
    rmSync(join(outputRoot, '_astro/client.js'));
    assert.equal(reusedAssetReferencesAvailable(plan, outputRoot), false);
    finalizeResearchSnapshot({ ...snapshot, articles: {} }, {
      outputRoot, pageCache, targetManifest, sourcePending: join(root, 'pending.json'),
    });
    assert.equal(existsSync(join(pageCache, 'index.html')), false);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
