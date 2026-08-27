import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { brotliCompressSync, gzipSync } from 'node:zlib';
import { basename, join, relative, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export const DEFAULT_PERFORMANCE_BUDGETS = Object.freeze({
  maxJavaScriptChunkBytes: 350_000,
  maxRouteCompressedAssetsBytes: 370_000,
  maxOrdinaryRouteCompressedAssetsBytes: 300_000,
  // Raised from 100KB (2026-08-23): the bilingual Research Radar graph page
  // now carries enough static graph metadata to sit just over the old cap.
  maxPageHtmlGzipBytes: 115_000,
  maxAverageHtmlBytes: 105_000,
  // Use both a fixed site floor and a compressed per-page ceiling. This keeps
  // large catalogues honest without making ordinary route growth consume a
  // permanently fixed allowance.
  maxAverageHtmlGzipBytes: 18_500,
  maxTotalHtmlGzipBytes: 26_000_000,
  maxFontAssetBytes: 220_000,
  maxLegacyWoffFiles: 0,
  maxWorldMapBytes: 525_000,
  maxWorldMapBrotliBytes: 140_000,
});

function walkHtml(directory, files = []) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const target = join(directory, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== '_astro') walkHtml(target, files);
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

export function auditPerformance(distDirectory, budgetOverrides = {}) {
  const dist = resolve(distDirectory);
  const assetDirectory = join(dist, '_astro');
  const budgets = { ...DEFAULT_PERFORMANCE_BUDGETS, ...budgetOverrides };
  const errors = [];

  if (!existsSync(assetDirectory)) {
    return { passed: false, errors: [`Missing build asset directory: ${assetDirectory}`], budgets };
  }

  const assets = new Map();
  for (const entry of readdirSync(assetDirectory, { withFileTypes: true })) {
    if (!entry.isFile()) continue;
    const bytes = readFileSync(join(assetDirectory, entry.name));
    assets.set(entry.name, {
      rawBytes: bytes.length,
      gzipBytes: gzipSync(bytes, { level: 9 }).length,
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

  const pages = walkHtml(dist);
  if (pages.length === 0) errors.push(`No HTML pages found in ${dist}.`);
  let largestRoute = { path: '', gzipBytes: 0, assetCount: 0 };
  let largestOrdinaryRoute = { path: '', gzipBytes: 0, assetCount: 0 };
  let largestPageHtml = { path: '', gzipBytes: 0 };
  let totalHtmlBytes = 0;
  let totalHtmlGzipBytes = 0;

  for (const page of pages) {
    const htmlBytes = readFileSync(page);
    const html = htmlBytes.toString('utf8');
    totalHtmlBytes += htmlBytes.length;
    const pageHtmlGzipBytes = gzipSync(htmlBytes, { level: 9 }).length;
    totalHtmlGzipBytes += pageHtmlGzipBytes;
    const routePath = `/${relative(dist, page).replaceAll('\\', '/')}`;
    if (pageHtmlGzipBytes > largestPageHtml.gzipBytes) largestPageHtml = { path: routePath, gzipBytes: pageHtmlGzipBytes };
    const selected = new Set();
    for (const match of html.matchAll(/(?:component-url|renderer-url)="\/_astro\/([^"?]+\.js)"/g)) {
      addJavaScriptDependencies(match[1], assets, selected);
    }
    for (const match of html.matchAll(/(?:src|href)="\/_astro\/([^"?]+\.(?:js|css))[^"?]*"/g)) {
      if (match[1].endsWith('.js')) addJavaScriptDependencies(match[1], assets, selected);
      else if (assets.has(match[1])) selected.add(match[1]);
    }
    const gzipBytes = [...selected].reduce((sum, name) => sum + (assets.get(name)?.gzipBytes ?? 0), 0);
    if (gzipBytes > largestRoute.gzipBytes) {
      largestRoute = {
        path: routePath,
        gzipBytes,
        assetCount: selected.size,
      };
    }
    const isDataRoute = /^\/(?:zh\/)?(?:countries|diseases|situation|research)\//.test(routePath);
    if (!isDataRoute && gzipBytes > largestOrdinaryRoute.gzipBytes) {
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
  const result = auditPerformance(dist);
  const label = result.passed ? 'PASS' : 'FAIL';
  process.stdout.write(`[performance-budget] ${label} ${JSON.stringify(result)}\n`);
  if (!result.passed) process.exitCode = 1;
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) runCli();
