import { openPageAuditCache } from './page-audit-cache.mjs';
import { createHash } from 'node:crypto';
import { openGzipSizeCache } from './gzip-size-cache.mjs';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { brotliCompressSync, gzipSync } from 'node:zlib';
import { basename, join, relative, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { isIndependentResearchRoute } from './check-seo-output.mjs';

export const DEFAULT_PERFORMANCE_BUDGETS = Object.freeze({
  maxJavaScriptChunkBytes: 350_000,
  // Disease pages carry the surveillance explorer plus a coverage map and a
  // seasonality heatmap. The 2026-10-23 release build measured that route at
  // 381,198 gzip bytes. 395KB still rejects a second ECharts core: zrender
  // alone is about 62KB gzip.
  maxRouteCompressedAssetsBytes: 395_000,
  maxOrdinaryRouteCompressedAssetsBytes: 300_000,
  // Raised from 100KB (2026-08-23): the multilingual Research Radar graph page
  // carries enough static graph metadata to sit just over the old cap.
  // Situation map pages stay inside this cap by fetching markers from
  // /situation/map-data.json. Embedding the three-year marker set (1412 points)
  // plus monitoring measured /zh/situation/index.html at 139,777 gzip bytes on
  // 2026-11-02 and is not the generated shape.
  maxPageHtmlGzipBytes: 115_000,
  maxAverageHtmlBytes: 105_000,
  // Measured 2026-11-11 after independent research pages leave this gate:
  // 4,109 main-site pages averaged 168,783 raw bytes and 20,731 gzip bytes.
  // The old 105KB/19KB averages only held while small article pages diluted
  // the catalogue. Per-page and route-asset caps stay unchanged.
  maxMainSiteAverageHtmlBytes: 180_000,
  maxMainSiteAverageHtmlGzipBytes: 22_000,
  // Use both a fixed site floor and a compressed per-page ceiling. The 18.7KB
  // baseline includes the complete en/zh/fr catalogue and deterministic CI
  // fixture while the independent
  // 115KB page cap and route-asset caps continue to catch local regressions.
  // Release builds stamp each page with a unique release id, source commit,
  // deployment branch, and build timestamp.  Keep enough headroom for those
  // bounded metadata values while preserving the same underlying page budget.
  // The 2026-10 jurisdiction expansion adds state/territory profile routes,
  // raising the full-site average to about 19.0KB while individual page and
  // route-asset limits remain unchanged.
  maxAverageHtmlGzipBytes: 19_050,
  maxTotalHtmlGzipBytes: 26_000_000,
  maxFontAssetBytes: 220_000,
  maxLegacyWoffFiles: 0,
  maxWorldMapBytes: 525_000,
  maxWorldMapBrotliBytes: 140_000,
});

// A local Situation fixture may deliberately reuse the current, much larger
// country/report catalogue while exercising only the Situation export.  Keep
// that test profile explicit and opt-in so it cannot weaken a production
// publication: per-page, JavaScript, route-asset, font and map limits remain
// unchanged.  The production build stage never sets this variable.
export const PERFORMANCE_BUDGET_PROFILES = Object.freeze({
  'large-local-fixture': Object.freeze({
    maxAverageHtmlBytes: 220_000,
    maxAverageHtmlGzipBytes: 30_000,
    maxTotalHtmlGzipBytes: 100_000_000,
  }),
});

export function profileBudgetOverrides(env = process.env) {
  const profile = env.GIDS_PERFORMANCE_BUDGET_PROFILE;
  if (!profile) return {};
  const overrides = PERFORMANCE_BUDGET_PROFILES[profile];
  if (!overrides) throw new Error(`Unknown GIDS_PERFORMANCE_BUDGET_PROFILE: ${profile}`);
  return overrides;
}

function walkHtml(directory, files = [], root = directory, independentResearch = false) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const target = join(directory, entry.name);
    if (independentResearch && isIndependentResearchRoute('/' + relative(root, target).replaceAll('\\', '/'))) continue;
    if (entry.isDirectory()) {
      if (entry.name !== '_astro') walkHtml(target, files, root, independentResearch);
    } else if (entry.name.endsWith('.html')) {
      files.push(target);
    }
  }
  return files;
}

function addJavaScriptDependencies(assetName, assets, selected) {
  if (selected.has(assetName)) return;
  const asset = assets.get(assetName);
  if (!asset) return;
  selected.add(assetName);
  if (!assetName.endsWith('.js')) return;

  for (const match of asset.text.matchAll(/(?:from\s*|import\s*\(?\s*)["']\.\/([^"']+\.js)["']/g)) {
    addJavaScriptDependencies(match[1], assets, selected);
  }
}

export function auditPerformance(distDirectory, budgetOverrides = {}, { gzipCache, pageCache, independentResearch = process.env.GIDS_RESEARCH_PUBLICATION_ENABLED === '1', env = process.env } = {}) {
  const dist = resolve(distDirectory);
  const assetDirectory = join(dist, '_astro');
  const budgets = {
    ...DEFAULT_PERFORMANCE_BUDGETS,
    ...(independentResearch ? {
      maxAverageHtmlBytes: DEFAULT_PERFORMANCE_BUDGETS.maxMainSiteAverageHtmlBytes,
      maxAverageHtmlGzipBytes: DEFAULT_PERFORMANCE_BUDGETS.maxMainSiteAverageHtmlGzipBytes,
    } : {}),
    ...profileBudgetOverrides(env),
    ...budgetOverrides,
  };
  const errors = [];

  if (!existsSync(assetDirectory)) {
    return { passed: false, errors: [`Missing build asset directory: ${assetDirectory}`], budgets };
  }

  const gzipSize = bytes => gzipCache ? gzipCache.measure(bytes) : gzipSync(bytes, { level: 9 }).length;
  const assets = new Map();
  for (const entry of readdirSync(assetDirectory, { withFileTypes: true })) {
    if (!entry.isFile()) continue;
    const bytes = readFileSync(join(assetDirectory, entry.name));
    assets.set(entry.name, {
      rawBytes: bytes.length,
      gzipBytes: gzipSize(bytes),
      text: entry.name.endsWith('.js') ? bytes.toString('utf8') : '',
    });
  }

  const javaScript = [...assets]
    .filter(([name]) => name.endsWith('.js'))
    .sort((left, right) => right[1].rawBytes - left[1].rawBytes);
  const largestJavaScript = javaScript[0] ?? ['', { rawBytes: 0, gzipBytes: 0 }];
  if (largestJavaScript[1].rawBytes > budgets.maxJavaScriptChunkBytes) {
    errors.push(
      `Largest JavaScript chunk ${largestJavaScript[0]} is ${largestJavaScript[1].rawBytes} bytes `
      + `(budget ${budgets.maxJavaScriptChunkBytes}).`,
    );
  }

  const fontAssets = [...assets].filter(([name]) => name.endsWith('.woff2'));
  const totalFontBytes = fontAssets.reduce((sum, [, asset]) => sum + asset.rawBytes, 0);
  if (totalFontBytes > budgets.maxFontAssetBytes) {
    errors.push(`WOFF2 assets total ${totalFontBytes} bytes (budget ${budgets.maxFontAssetBytes}).`);
  }

  const legacyWoffFiles = [...assets.keys()].filter((name) => name.endsWith('.woff'));
  if (legacyWoffFiles.length > budgets.maxLegacyWoffFiles) {
    errors.push(
      `Found ${legacyWoffFiles.length} legacy WOFF files (budget ${budgets.maxLegacyWoffFiles}): `
      + legacyWoffFiles.slice(0, 5).join(', '),
    );
  }

  const worldMapPath = join(dist, 'data', 'world.json');
  let worldMap = { rawBytes: 0, brotliBytes: 0 };
  if (!existsSync(worldMapPath)) {
    errors.push(`Missing optimized world map: ${worldMapPath}`);
  } else {
    const worldMapBytes = readFileSync(worldMapPath);
    worldMap = {
      rawBytes: worldMapBytes.length,
      brotliBytes: brotliCompressSync(worldMapBytes).length,
    };
    if (worldMap.rawBytes > budgets.maxWorldMapBytes) {
      errors.push(`World map is ${worldMap.rawBytes} bytes (budget ${budgets.maxWorldMapBytes}).`);
    }
    if (worldMap.brotliBytes > budgets.maxWorldMapBrotliBytes) {
      errors.push(
        `World map is ${worldMap.brotliBytes} Brotli bytes (budget ${budgets.maxWorldMapBrotliBytes}).`,
      );
    }
  }

  const pages = walkHtml(dist, [], dist, independentResearch);
  if (pages.length === 0) errors.push(`No HTML pages found in ${dist}.`);
  let largestRoute = { path: '', gzipBytes: 0, assetCount: 0 };
  let largestOrdinaryRoute = { path: '', gzipBytes: 0, assetCount: 0 };
  let largestPageHtml = { path: '', gzipBytes: 0 };
  let totalHtmlBytes = 0;
  let totalHtmlGzipBytes = 0;

  const graphs = new Map();
  const extract = bytes => {
    const html = bytes.toString('utf8');
    const names = new Set();
    for (const match of html.matchAll(/(?:component-url|renderer-url)="\/_astro\/([^"?]+\.js)"/g)) names.add(match[1]);
    for (const match of html.matchAll(/(?:src|href)="\/_astro\/([^"?]+\.(?:js|css))[^"?]*"/g)) names.add(match[1]);
    // RegExp captures can retain the entire HTML backing string. Persist only
    // detached asset names, otherwise a catalogue cache keeps gigabytes alive.
    return { rawBytes: bytes.length, gzipBytes: gzipSize(bytes),
      assets: [...names].map(name => Buffer.from(name, 'utf8').toString('utf8')).sort() };
  };
  for (const page of pages) {
    const facts = pageCache ? pageCache.read(page, extract) : extract(readFileSync(page));
    totalHtmlBytes += facts.rawBytes;
    const pageHtmlGzipBytes = facts.gzipBytes;
    totalHtmlGzipBytes += pageHtmlGzipBytes;
    const routePath = `/${relative(dist, page).replaceAll('\\', '/')}`;
    if (pageHtmlGzipBytes > largestPageHtml.gzipBytes) largestPageHtml = { path: routePath, gzipBytes: pageHtmlGzipBytes };
    const graphKey = JSON.stringify(facts.assets);
    let selected = graphs.get(graphKey);
    if (!selected) {
      selected = new Set();
      for (const name of facts.assets) {
        if (name.endsWith('.js')) addJavaScriptDependencies(name, assets, selected);
        else if (assets.has(name)) selected.add(name);
      }
      graphs.set(graphKey, selected);
    }
    const gzipBytes = [...selected].reduce((sum, name) => sum + (assets.get(name)?.gzipBytes ?? 0), 0);
    if (gzipBytes > largestRoute.gzipBytes) {
      largestRoute = {
        path: routePath,
        gzipBytes,
        assetCount: selected.size,
      };
    }
    // Compare is a chart workspace, same class as disease, country, situation,
    // and research pages. Brochure routes stay on the ordinary cap.
    const isChartRoute = /^\/(?:(?:zh|fr)\/)?(?:compare|countries|diseases|situation|research)\//.test(routePath);
    if (!isChartRoute && gzipBytes > largestOrdinaryRoute.gzipBytes) {
      largestOrdinaryRoute = { path: routePath, gzipBytes, assetCount: selected.size };
    }
  }

  if (largestRoute.gzipBytes > budgets.maxRouteCompressedAssetsBytes) {
    errors.push(
      `Largest route asset graph ${largestRoute.path} is ${largestRoute.gzipBytes} gzip bytes `
      + `(budget ${budgets.maxRouteCompressedAssetsBytes}).`,
    );
  }
  if (largestOrdinaryRoute.gzipBytes > budgets.maxOrdinaryRouteCompressedAssetsBytes) {
    errors.push(`Largest ordinary route asset graph ${largestOrdinaryRoute.path} is ${largestOrdinaryRoute.gzipBytes} gzip bytes (budget ${budgets.maxOrdinaryRouteCompressedAssetsBytes}).`);
  }
  if (largestPageHtml.gzipBytes > budgets.maxPageHtmlGzipBytes) {
    errors.push(`Largest page HTML ${largestPageHtml.path} is ${largestPageHtml.gzipBytes} gzip bytes (budget ${budgets.maxPageHtmlGzipBytes}).`);
  }
  const averageHtmlBytes = pages.length > 0 ? Math.round(totalHtmlBytes / pages.length) : 0;
  const averageHtmlGzipBytes = pages.length > 0 ? Math.round(totalHtmlGzipBytes / pages.length) : 0;
  if (averageHtmlBytes > budgets.maxAverageHtmlBytes) {
    errors.push(
      `Generated HTML averages ${averageHtmlBytes} bytes per page `
      + `(budget ${budgets.maxAverageHtmlBytes}).`,
    );
  }
  if (averageHtmlGzipBytes > budgets.maxAverageHtmlGzipBytes) {
    errors.push(
      `Generated HTML averages ${averageHtmlGzipBytes} gzip bytes per page `
      + `(budget ${budgets.maxAverageHtmlGzipBytes}).`,
    );
  }
  const effectiveTotalHtmlGzipBudget = Math.max(
    budgets.maxTotalHtmlGzipBytes,
    pages.length * budgets.maxAverageHtmlGzipBytes,
  );
  if (totalHtmlGzipBytes > effectiveTotalHtmlGzipBudget) {
    errors.push(
      `Generated HTML totals ${totalHtmlGzipBytes} gzip bytes `
      + `(budget ${effectiveTotalHtmlGzipBudget}; fixed floor ${budgets.maxTotalHtmlGzipBytes}).`,
    );
  }

  return {
    passed: errors.length === 0,
    errors,
    budgets,
    pages: pages.length,
    javaScriptChunks: javaScript.length,
    largestJavaScript: {
      file: basename(largestJavaScript[0]),
      rawBytes: largestJavaScript[1].rawBytes,
      gzipBytes: largestJavaScript[1].gzipBytes,
    },
    fonts: {
      woff2Files: fontAssets.length,
      legacyWoffFiles: legacyWoffFiles.length,
      rawBytes: totalFontBytes,
    },
    worldMap,
    html: {
      rawBytes: totalHtmlBytes,
      gzipBytes: totalHtmlGzipBytes,
      averageRawBytes: averageHtmlBytes,
      averageGzipBytes: averageHtmlGzipBytes,
      gzipBudgetBytes: effectiveTotalHtmlGzipBudget,
      largestPage: largestPageHtml,
    },
    largestRoute,
    largestOrdinaryRoute,
  };
}

function runCli() {
  const distIndex = process.argv.indexOf('--dist');
  const dist = distIndex >= 0 && process.argv[distIndex + 1]
    ? process.argv[distIndex + 1]
    : resolve(import.meta.dirname, '..', 'dist');
  const gzipCache = openGzipSizeCache(resolve(import.meta.dirname, '../../data/cache/build-gzip-sizes.json'));
  const version = createHash('sha256').update(readFileSync(new URL(import.meta.url))).update(process.versions.zlib).digest('hex');
  const pageCache = openPageAuditCache(resolve(import.meta.dirname, '../../data/cache/performance-page-facts.json'), version);
  const result = auditPerformance(dist, {}, { gzipCache, pageCache });
  pageCache.save();
  result.pageFacts = pageCache.stats;
  gzipCache.save();
  result.compressionCache = gzipCache.stats;
  const label = result.passed ? 'PASS' : 'FAIL';
  process.stdout.write(`[performance-budget] ${label} ${JSON.stringify(result)}\n`);
  if (!result.passed) process.exitCode = 1;
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) runCli();
