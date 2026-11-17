import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, relative, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { tags } from './check-seo-output.mjs';

// Checking existing references alone cannot detect a build that omitted every
// stylesheet reference. Require the global layout rules used by our templates.
export function auditBuildAssets(directory) {
  const root = resolve(directory), errors = [], css = new Map();
  let pages = 0;
  const baseRules = body => /\.skip-link(?:[\s:{.,#\[])/.test(body);
  const visit = folder => {
    for (const entry of readdirSync(folder, { withFileTypes: true })) {
      const file = resolve(folder, entry.name);
      if (entry.isDirectory()) { if (entry.name !== '_astro') visit(file); continue; }
      if (!entry.name.endsWith('.html')) continue;
      pages++;
      const html = readFileSync(file, 'utf8'), route = relative(root, file);
      let hasBase = [...html.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/gi)].some(match => baseRules(match[1]));
      for (const tag of [...tags(html, 'link'), ...tags(html, 'script')]) {
        const stylesheet = tag.rel?.split(/\s+/).includes('stylesheet');
        const href = stylesheet ? tag.href : tag.src;
        if (!href || /^(?:[a-z]+:)?\/\//i.test(href) || !href.includes('_astro/')) continue;
        const asset = resolve(href.startsWith('/') ? root : dirname(file), href.replace(/^\//, '').split(/[?#]/)[0]);
        if (!asset.startsWith(root + '/') || !existsSync(asset)) {
          errors.push(`${route}: missing local build asset ${href}`); continue;
        }
        if (stylesheet) {
          if (!css.has(asset)) css.set(asset, baseRules(readFileSync(asset, 'utf8')));
          hasBase ||= css.get(asset);
        }
      }
      if (tags(html, 'a').some(tag => tag.class?.split(/\s+/).includes('skip-link')) && !hasBase)
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
