import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { brotliCompressSync, gzipSync } from 'node:zlib';
import { basename, join, relative, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export const DEFAULT_PERFORMANCE_BUDGETS = Object.freeze({
  maxJavaScriptChunkBytes: 500_000,
  maxRouteCompressedAssetsBytes: 370_000,
  maxTotalHtmlBytes: 120_000_000,
  maxTotalHtmlGzipBytes: 24_000_000,
  maxFontAssetBytes: 400_000,
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
  let totalHtmlBytes = 0;
  let totalHtmlGzipBytes = 0;

  for (const page of pages) {
    const htmlBytes = readFileSync(page);
    const html = htmlBytes.toString('utf8');
    totalHtmlBytes += htmlBytes.length;
    totalHtmlGzipBytes += gzipSync(htmlBytes, { level: 9 }).length;
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
        path: `/${relative(dist, page).replaceAll('\\', '/')}`,
        gzipBytes,
        assetCount: selected.size,
      };
    }
  }

  if (largestRoute.gzipBytes > budgets.maxRouteCompressedAssetsBytes) {
    errors.push(
      `Largest route asset graph ${largestRoute.path} is ${largestRoute.gzipBytes} gzip bytes `
      + `(budget ${budgets.maxRouteCompressedAssetsBytes}).`,
    );
  }
  if (totalHtmlBytes > budgets.maxTotalHtmlBytes) {
    errors.push(
      `Generated HTML totals ${totalHtmlBytes} bytes (budget ${budgets.maxTotalHtmlBytes}).`,
    );
  }
  if (totalHtmlGzipBytes > budgets.maxTotalHtmlGzipBytes) {
    errors.push(
      `Generated HTML totals ${totalHtmlGzipBytes} gzip bytes `
      + `(budget ${budgets.maxTotalHtmlGzipBytes}).`,
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
      averageRawBytes: pages.length > 0 ? Math.round(totalHtmlBytes / pages.length) : 0,
    },
    largestRoute,
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
