import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { attributes, tags } from './check-seo-output.mjs';

const BASE_RULES_RE = /\.skip-link(?:[\s:{.,#\[])/;
const STYLE_BLOCK_RE = /<style\b[^>]*>([\s\S]*?)<\/style>/gi;
const LINK_OR_SCRIPT_TAG_RE = /<(link|script)\b(?:[^>"']|"[^"]*"|'[^']*')*>/gi;
const EXTERNAL_URL_RE = /^(?:[a-z]+:)?\/\//i;
const WHITESPACE_RE = /\s+/;

// Checking existing references alone cannot detect a build that omitted every
// stylesheet reference. Require the global layout rules used by our templates.
export function auditBuildAssets(directory) {
  const root = resolve(directory), errors = [], css = new Map(), assetExists = new Map();
  const rootPrefix = root + '/';
  let pages = 0;
  const baseRules = body => BASE_RULES_RE.test(body);
  const hasAsset = asset => {
    let present = assetExists.get(asset);
    if (present === undefined) {
      present = existsSync(asset);
      assetExists.set(asset, present);
    }
    return present;
  };
  const visit = folder => {
    for (const entry of readdirSync(folder, { withFileTypes: true })) {
      const file = join(folder, entry.name);
      if (entry.isDirectory()) { if (entry.name !== '_astro') visit(file); continue; }
      if (!entry.name.endsWith('.html')) continue;
      pages++;
      const html = readFileSync(file, 'utf8'), route = relative(root, file);
      let hasBase = false;
      if (html.includes('_astro/') || html.includes('&')) {
        const scriptErrors = [];
        for (const match of html.matchAll(LINK_OR_SCRIPT_TAG_RE)) {
          const rawTag = match[0];
          if (!rawTag.includes('_astro/') && !rawTag.includes('&')) continue;
          const isScript = match[1].length === 6;
          const tag = attributes(rawTag);
          const stylesheet = tag.rel?.split(WHITESPACE_RE).includes('stylesheet');
          const href = stylesheet ? tag.href : tag.src;
          if (!href || EXTERNAL_URL_RE.test(href) || !href.includes('_astro/')) continue;
          const asset = resolve(href.startsWith('/') ? root : dirname(file), href.replace(/^\//, '').split(/[?#]/)[0]);
          if (!asset.startsWith(rootPrefix) || !hasAsset(asset)) {
            (isScript ? scriptErrors : errors).push(`${route}: missing local build asset ${href}`);
            continue;
          }
          if (stylesheet) {
            if (!css.has(asset)) css.set(asset, baseRules(readFileSync(asset, 'utf8')));
            hasBase ||= css.get(asset);
          }
        }
        if (scriptErrors.length) errors.push(...scriptErrors);
      }
      if (!hasBase && html.includes('.skip-link')) {
        for (const match of html.matchAll(STYLE_BLOCK_RE)) {
          if (baseRules(match[1])) { hasBase = true; break; }
        }
      }
      if (!hasBase && tags(html, 'a').some(tag => tag.class?.split(WHITESPACE_RE).includes('skip-link')))
        errors.push(`${route}: missing bundled global layout CSS (theme variables cannot replace it)`);
    }
  };
  visit(root);
  return { passed: errors.length === 0, pages, errors };
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  const result = auditBuildAssets(resolve(import.meta.dirname, '../dist'));
  console.log(`[build-assets] ${result.passed ? 'PASS' : 'FAIL'} ${JSON.stringify(result)}`);
  if (!result.passed) process.exitCode = 1;
}
