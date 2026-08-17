import assert from 'node:assert/strict';
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

test('fails closed when generated HTML exceeds the all-site budget', (t) => {
  const directory = fixture();
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  writeFileSync(join(directory, 'index.html'), '<main>' + 'catalogue-entry-'.repeat(20) + '</main>');

  const result = auditPerformance(directory, {
    maxJavaScriptChunkBytes: 1_000,
    maxRouteCompressedAssetsBytes: 1_000,
    maxTotalHtmlBytes: 100,
    maxTotalHtmlGzipBytes: 10,
    maxWorldMapBytes: 1_000,
    maxWorldMapBrotliBytes: 1_000,
  });
  assert.equal(result.passed, false);
  assert.match(result.errors.join('\n'), /Generated HTML totals/);
  assert.ok(result.html.rawBytes > 100);
  assert.ok(result.html.gzipBytes > 10);
});
