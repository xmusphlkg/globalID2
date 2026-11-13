import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { auditPerformance, profileBudgetOverrides } from './check-performance-budgets.mjs';
import { openPageAuditCache } from './page-audit-cache.mjs';

function fixture() {
  const directory = mkdtempSync(join(tmpdir(), 'gids-performance-budget-'));
  mkdirSync(join(directory, '_astro'));
  mkdirSync(join(directory, 'data'));
  writeFileSync(join(directory, 'data', 'world.json'), '{"type":"FeatureCollection","features":[]}');
  return directory;
}

test('large local fixture profile is explicit and leaves production budgets unchanged', () => {
  assert.deepEqual(profileBudgetOverrides({}), {});
  const profile = profileBudgetOverrides({ GIDS_PERFORMANCE_BUDGET_PROFILE: 'large-local-fixture' });
  assert.equal(profile.maxAverageHtmlBytes, 220_000);
  assert.equal(profile.maxAverageHtmlGzipBytes, 30_000);
  assert.equal(profile.maxTotalHtmlGzipBytes, 100_000_000);
  assert.throws(() => profileBudgetOverrides({ GIDS_PERFORMANCE_BUDGET_PROFILE: 'unknown' }), /Unknown/);
});

test('persisted asset facts do not retain the complete HTML catalogue', t => {
  const directory = fixture();
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  const padding = 'x'.repeat(1024 * 1024);
  for (let i = 0; i < 96; i++) {
    writeFileSync(join(directory, `${i}.html`), `<script src="/_astro/catalogue-entry-abcdefghij.js"></script><main>${i}${padding}</main>`);
  }
  const script = `import {auditPerformance} from ${JSON.stringify(new URL('./check-performance-budgets.mjs', import.meta.url).href)};
    import {openPageAuditCache} from ${JSON.stringify(new URL('./page-audit-cache.mjs', import.meta.url).href)};
    const cache=openPageAuditCache(process.argv[1]+'/facts.json','test');
    const result=auditPerformance(process.argv[1],{}, {pageCache:cache});
    if(result.pages!==96 || cache.stats.misses!==96) throw new Error('Missing catalogue entries');
    cache.save();`;
  const result = spawnSync(process.execPath, ['--max-old-space-size=64', '--input-type=module', '-e', script, directory],
    { encoding: 'utf8', timeout: 30000, env: { ...process.env, GIDS_BUILD_FINGERPRINT_CACHE: join(directory, 'fingerprints.json') } });
  assert.equal(result.status, 0, result.stderr || String(result.error));
});

test('cached page facts still enforce current budgets and changed asset dependencies', t => {
  const directory = fixture();
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  writeFileSync(join(directory, 'index.html'), '<script src="/_astro/entry.js"></script>');
  writeFileSync(join(directory, '_astro', 'entry.js'), 'export const ready = true;');
  const path = join(directory, 'facts.json');
  const first = openPageAuditCache(path, 'test');
  assert.equal(auditPerformance(directory, {}, { pageCache: first }).passed, true);
  first.save();
  const cached = openPageAuditCache(path, 'test');
  assert.equal(auditPerformance(directory, { maxPageHtmlGzipBytes: 1 }, { pageCache: cached }).passed, false);
  assert.equal(cached.stats.hits, 1);
  writeFileSync(join(directory, '_astro', 'entry.js'), 'import "./large.js";');
  writeFileSync(join(directory, '_astro', 'large.js'), randomBytes(2000));
  const changed = auditPerformance(directory, { maxRouteCompressedAssetsBytes: 100 }, { pageCache: cached });
  assert.equal(changed.passed, false);
  assert.equal(changed.largestRoute.assetCount, 2);
});

test('audits the complete dependency graph referenced by an Astro island', (t) => {
  const directory = fixture();
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  writeFileSync(join(directory, '_astro', 'entry.js'), 'import "./shared.js";');
  writeFileSync(join(directory, '_astro', 'shared.js'), 'export const ready = true;');
  writeFileSync(join(directory, '_astro', 'base.css'), 'body{color:#123}');
  writeFileSync(
    join(directory, 'index.html'),
    '<link href="/_astro/base.css" rel="stylesheet"><astro-island component-url="/_astro/entry.js"></astro-island>',
  );

  const result = auditPerformance(directory, {
    maxJavaScriptChunkBytes: 1_000,
    maxRouteCompressedAssetsBytes: 1_000,
    maxWorldMapBytes: 1_000,
    maxWorldMapBrotliBytes: 1_000,
  });
  assert.equal(result.passed, true);
  assert.equal(result.pages, 1);
  assert.equal(result.largestRoute.assetCount, 3);
});

test('fails closed for oversized chunks and legacy font artifacts', (t) => {
  const directory = fixture();
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  writeFileSync(join(directory, '_astro', 'oversized.js'), 'x'.repeat(101));
  writeFileSync(join(directory, '_astro', 'legacy.woff'), 'font');
  writeFileSync(join(directory, 'index.html'), '<script src="/_astro/oversized.js"></script>');

  const result = auditPerformance(directory, {
    maxJavaScriptChunkBytes: 100,
    maxRouteCompressedAssetsBytes: 1_000,
    maxWorldMapBytes: 1_000,
    maxWorldMapBrotliBytes: 1_000,
  });
  assert.equal(result.passed, false);
  assert.match(result.errors.join('\n'), /Largest JavaScript chunk/);
  assert.match(result.errors.join('\n'), /legacy WOFF/);
});

test('fails closed when generated HTML exceeds average and compressed all-site budgets', (t) => {
  const directory = fixture();
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  writeFileSync(join(directory, 'index.html'), '<main>' + 'catalogue-entry-'.repeat(20) + '</main>');

  const result = auditPerformance(directory, {
    maxJavaScriptChunkBytes: 1_000,
    maxRouteCompressedAssetsBytes: 1_000,
    maxAverageHtmlBytes: 100,
    maxAverageHtmlGzipBytes: 10,
    maxTotalHtmlGzipBytes: 10,
    maxWorldMapBytes: 1_000,
    maxWorldMapBrotliBytes: 1_000,
  });
  assert.equal(result.passed, false);
  assert.match(result.errors.join('\n'), /Generated HTML averages/);
  assert.ok(result.html.rawBytes > 100);
  assert.ok(result.html.gzipBytes > 10);
});

test('treats compare workspaces as chart routes in every locale', (t) => {
  const directory = fixture();
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  writeFileSync(join(directory, '_astro', 'chart.js'), randomBytes(2_000));
  const island = '<astro-island component-url="/_astro/chart.js"></astro-island>';
  mkdirSync(join(directory, 'compare'));
  mkdirSync(join(directory, 'zh', 'compare'), { recursive: true });
  mkdirSync(join(directory, 'fr', 'compare'), { recursive: true });
  writeFileSync(join(directory, 'index.html'), '<main>home</main>');
  for (const route of ['compare', 'zh/compare', 'fr/compare']) {
    writeFileSync(join(directory, route, 'index.html'), island);
  }

  const result = auditPerformance(directory, {
    maxJavaScriptChunkBytes: 100_000,
    maxRouteCompressedAssetsBytes: 100_000,
    maxOrdinaryRouteCompressedAssetsBytes: 500,
    maxWorldMapBytes: 1_000,
    maxWorldMapBrotliBytes: 1_000,
  });

  assert.equal(result.passed, true);
  assert.equal(result.largestOrdinaryRoute.gzipBytes, 0);
  assert.ok(result.largestRoute.gzipBytes > 500);
});

test('keeps a heavy brochure page on the ordinary route cap', (t) => {
  const directory = fixture();
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  writeFileSync(join(directory, '_astro', 'chart.js'), randomBytes(2_000));
  mkdirSync(join(directory, 'about'));
  writeFileSync(
    join(directory, 'about', 'index.html'),
    '<astro-island component-url="/_astro/chart.js"></astro-island>',
  );

  const result = auditPerformance(directory, {
    maxJavaScriptChunkBytes: 100_000,
    maxRouteCompressedAssetsBytes: 100_000,
    maxOrdinaryRouteCompressedAssetsBytes: 500,
    maxWorldMapBytes: 1_000,
    maxWorldMapBrotliBytes: 1_000,
  });

  assert.equal(result.passed, false);
  assert.match(result.errors.join('\n'), /ordinary route asset graph \/about\/index\.html/);
});

test('scales the all-site gzip allowance while preserving a per-page ceiling', (t) => {
  const directory = fixture();
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  writeFileSync(join(directory, 'index.html'), '<main>compact</main>');
  writeFileSync(join(directory, 'second.html'), '<main>compact</main>');

  const result = auditPerformance(directory, {
    maxJavaScriptChunkBytes: 1_000,
    maxRouteCompressedAssetsBytes: 1_000,
    maxAverageHtmlBytes: 1_000,
    maxAverageHtmlGzipBytes: 1_000,
    maxTotalHtmlGzipBytes: 1,
    maxWorldMapBytes: 1_000,
    maxWorldMapBrotliBytes: 1_000,
  });

  assert.equal(result.passed, true);
  assert.equal(result.html.gzipBudgetBytes, 2_000);
  assert.ok(result.html.averageGzipBytes < 1_000);
});

test('independent research pages stay out of the main-site performance gate', (t) => {
  const directory = fixture();
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  writeFileSync(join(directory, 'index.html'), '<main>home</main>');
  for (const route of ['research', 'zh/research', 'fr/research/graph']) {
    mkdirSync(join(directory, route), { recursive: true });
    writeFileSync(join(directory, route, 'index.html'), `<main>${'research-page-'.repeat(400)}</main>`);
  }
  const budgets = {
    maxJavaScriptChunkBytes: 1_000,
    maxRouteCompressedAssetsBytes: 1_000,
    maxAverageHtmlBytes: 200,
    maxAverageHtmlGzipBytes: 1_000,
    maxTotalHtmlGzipBytes: 1_000_000,
    maxPageHtmlGzipBytes: 100_000,
    maxWorldMapBytes: 1_000,
    maxWorldMapBrotliBytes: 1_000,
  };
  const ignored = auditPerformance(directory, budgets, { independentResearch: true });
  assert.equal(ignored.pages, 1);
  assert.equal(ignored.passed, true);
  const calibrated = auditPerformance(directory, {
    maxJavaScriptChunkBytes: 1_000,
    maxRouteCompressedAssetsBytes: 1_000,
    maxWorldMapBytes: 1_000,
    maxWorldMapBrotliBytes: 1_000,
  }, { independentResearch: true });
  assert.equal(calibrated.budgets.maxAverageHtmlBytes, 180_000);
  assert.equal(calibrated.budgets.maxAverageHtmlGzipBytes, 22_000);
  assert.equal(auditPerformance(directory, {}, { independentResearch: false }).budgets.maxAverageHtmlGzipBytes, 19_050);
  const included = auditPerformance(directory, budgets, { independentResearch: false });
  assert.equal(included.pages, 4);
  assert.equal(included.passed, false);
  assert.match(included.errors.join('\n'), /Generated HTML averages/);
});

test('cached compression sizes still enforce changed budgets', async t => {
  const {openGzipSizeCache}=await import('./gzip-size-cache.mjs');
  const directory=fixture();
  t.after(()=>rmSync(directory,{recursive:true,force:true}));
  writeFileSync(join(directory,'index.html'),'<main>some content</main>');
  const cache=openGzipSizeCache(join(directory,'sizes.json'));
  assert.equal(auditPerformance(directory,{}, {gzipCache:cache}).passed,true);
  const strict=auditPerformance(directory,{maxPageHtmlGzipBytes:1},{gzipCache:cache});
  assert.equal(strict.passed,false);
  assert.match(strict.errors.join('\n'),/Largest page HTML/);
  assert.ok(cache.stats.hits>0);
});
