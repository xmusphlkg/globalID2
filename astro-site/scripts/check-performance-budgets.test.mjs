import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { auditPerformance } from './check-performance-budgets.mjs';

function fixture() {
  const directory = mkdtempSync(join(tmpdir(), 'gids-performance-budget-'));
  mkdirSync(join(directory, '_astro'));
  mkdirSync(join(directory, 'data'));
  writeFileSync(join(directory, 'data', 'world.json'), '{"type":"FeatureCollection","features":[]}');
  return directory;
}

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
