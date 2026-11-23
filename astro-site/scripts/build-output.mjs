import { existsSync, lstatSync, readFileSync, readdirSync, renameSync, rmSync } from 'node:fs';
import { dirname, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

function* htmlFiles(root) {
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const path = resolve(root, entry.name);
    if (entry.isDirectory()) yield* htmlFiles(path);
    else if (entry.isFile() && entry.name.endsWith('.html')) yield path;
  }
}

// A theme-variable stylesheet or Astro's display:contents island style does
// not provide the shared page layout. Check the actual CSS bytes referenced
// by every BaseLayout document, including restored incremental pages.
export function validateStylesheets(root, { requiredPaths = [] } = {}) {
  root = resolve(root);
  for (const path of requiredPaths) {
    const file = resolve(root, path);
    if (!file.startsWith(root + sep) || !existsSync(file) || !lstatSync(file).isFile()) {
      throw new Error(`Build output omitted required route: ${path}`);
    }
  }
  const assets = new Map();
  let pages = 0;
  let styledPages = 0;
  for (const path of htmlFiles(root)) {
    const html = readFileSync(path, 'utf8');
    pages += 1;
    let baseStyles = /<style\b[^>]*>[\s\S]*?\.site-header\b[\s\S]*?<\/style>/i.test(html);
    for (const match of html.matchAll(/<link\b[^>]*>/gi)) {
      const attributes = Object.fromEntries([...match[0].matchAll(/([\w-]+)\s*=\s*(["'])(.*?)\2/g)].map(item => [item[1].toLowerCase(), item[3]]));
      if (!(attributes.rel ?? '').toLowerCase().split(/\s+/).includes('stylesheet')) continue;
      const href = attributes.href ?? '';
      if (/^(?:https?:|\/\/|data:)/i.test(href)) continue;
      const pathname = decodeURIComponent(href.split(/[?#]/)[0]);
      const asset = pathname.startsWith('/') ? resolve(root, '.' + pathname) : resolve(dirname(path), pathname);
      if (!asset.startsWith(root + sep)) throw new Error(`Stylesheet escapes build output: ${href}`);
      if (!assets.has(asset)) {
        if (!existsSync(asset) || !lstatSync(asset).isFile()) throw new Error(`Missing stylesheet ${href} in ${relative(root, path)}`);
        const css = readFileSync(asset, 'utf8');
        if (!css.trim()) throw new Error(`Empty stylesheet ${href} in ${relative(root, path)}`);
        assets.set(asset, /\.site-header\b/.test(css));
      }
      baseStyles ||= assets.get(asset);
    }
    if (/class=["'][^"']*\bsite-header\b/.test(html)) {
      styledPages += 1;
      if (!baseStyles) throw new Error(`Missing base layout CSS in ${relative(root, path)}; theme variables alone cannot style the page`);
    }
  }
  if (!pages) throw new Error('Build output has no HTML pages');
  return { pages, styledPages, stylesheets: assets.size };
}

// Call with the site build lock held, before creating this build's candidate.
export function cleanAbandonedBuilds(site) {
  for (const entry of readdirSync(site, { withFileTypes: true })) {
    if (!entry.isDirectory() || !/^\.dist-build-\d+-\d+$/.test(entry.name)) continue;
    const candidate = resolve(site, entry.name);
    // An interrupted promotion can leave the previous dist beside the candidate.
    if (existsSync(candidate + '.previous')) continue;
    try { rmSync(candidate, { recursive: true, force: true }); }
    catch (error) { console.warn(`Could not remove abandoned build ${candidate}: ${error.message}`); }
  }
}

export function promoteBuildOutput(candidate, destination, {
  rename = renameSync, remove = rmSync, warn = console.warn,
} = {}) {
  candidate = resolve(candidate);
  destination = resolve(destination);
  if (candidate === destination || dirname(candidate) !== dirname(destination)) throw new Error('Build output promotion requires sibling directories');
  if (!existsSync(candidate) || !lstatSync(candidate).isDirectory()) throw new Error('Build candidate directory is missing');
  if (existsSync(destination) && !lstatSync(destination).isDirectory()) throw new Error('Build destination is not a directory');
  const previous = `${candidate}.previous`;
  if (existsSync(previous)) throw new Error('Previous build backup already exists');
  const hadPrevious = existsSync(destination);
  // Never remove destination in the failure path: the first rename can fail
  // while the original directory is still there and fully intact.
  if (hadPrevious) rename(destination, previous);
  try { rename(candidate, destination); }
  catch (error) {
    if (hadPrevious) rename(previous, destination);
    throw error;
  }
  // A cleanup failure must not roll back to a backup that may be partly
  // deleted. Keep the verified new output and report the retained backup.
  if (hadPrevious) {
    try { remove(previous, { recursive: true, force: true }); }
    catch (error) { warn(`Build promoted; could not remove backup ${previous}: ${error.message}`); }
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const result = validateStylesheets(process.argv[2] || resolve(import.meta.dirname, '../dist'));
  console.log(`[stylesheets] PASS ${JSON.stringify(result)}`);
}
