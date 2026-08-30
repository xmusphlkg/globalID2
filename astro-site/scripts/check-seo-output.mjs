import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { extname, join, relative, resolve } from 'node:path';

const SITE_ORIGIN = 'https://globalinfectiousdisease.com';

function walk(directory, suffix) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? walk(path, suffix) : entry.name.endsWith(suffix) ? [path] : [];
  });
}

function decodeHtml(value) {
  const named = { amp: '&', apos: "'", gt: '>', lt: '<', quot: '"' };
  return String(value ?? '').replace(/&(#x[\da-f]+|#\d+|amp|apos|gt|lt|quot);/gi, (_, entity) => {
    if (entity[0] !== '#') return named[entity.toLowerCase()] ?? _;
    const hexadecimal = entity[1].toLowerCase() === 'x';
    return String.fromCodePoint(Number.parseInt(entity.slice(hexadecimal ? 2 : 1), hexadecimal ? 16 : 10));
  });
}

function attributes(tag) {
  const output = {};
  for (const match of tag.matchAll(/([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g)) {
    output[match[1].toLowerCase()] = decodeHtml(match[2] ?? match[3] ?? match[4] ?? '');
  }
  return output;
}

function tags(html, name) {
  return [...html.matchAll(new RegExp(`<${name}\\b[^>]*>`, 'gi'))].map(match => attributes(match[0]));
}

function routeFor(file, dist) {
  const path = relative(dist, file).replaceAll('\\', '/');
  if (path === 'index.html') return '/';
  if (path.endsWith('/index.html')) return `/${path.slice(0, -10)}`;
  return `/${path}`;
}

function targetExists(pathname, dist) {
  let decoded;
  try { decoded = decodeURIComponent(pathname); } catch { return false; }
  const path = decoded.replace(/^\/+/, '');
  const candidates = path
    ? [resolve(dist, path)]
    : [resolve(dist, 'index.html')];
  if (path.endsWith('/')) candidates.push(resolve(dist, path, 'index.html'));
  else if (!extname(path)) candidates.push(resolve(dist, `${path}.html`), resolve(dist, path, 'index.html'));
  return candidates.some(candidate => candidate.startsWith(`${dist}/`) || candidate === dist)
    && candidates.some(candidate => existsSync(candidate) && statSync(candidate).isFile());
}

function redirectMap(dist) {
  const output = new Map();
  const file = resolve(dist, '_redirects');
  if (!existsSync(file)) return output;
  for (const rawLine of readFileSync(file, 'utf8').split('\n')) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const [source, destination, status] = line.split(/\s+/);
    if (/^30[1278]$/.test(status)) output.set(source, destination);
  }
  return output;
}

function isRedirectSource(route, redirects) {
  return redirects.has(route);
}

export function auditSeoOutput(distDirectory) {
  const dist = resolve(distDirectory);
  const redirects = redirectMap(dist);
  const errors = [];
  const pages = new Map();
  const indexableTitles = new Map();
  const indexableCanonicals = new Map();
  let indexable = 0;
  let noindex = 0;

  for (const file of walk(dist, '.html')) {
    const route = routeFor(file, dist);
    if (isRedirectSource(route, redirects)) continue;
    const html = readFileSync(file, 'utf8');
    const title = decodeHtml(html.match(/<title>([\s\S]*?)<\/title>/i)?.[1] ?? '').replace(/\s+/g, ' ').trim();
    const meta = tags(html, 'meta');
    const link = tags(html, 'link');
    const robots = meta.find(item => item.name === 'robots')?.content?.toLowerCase() ?? '';
    const isIndexable = !robots.includes('noindex');
    const description = meta.filter(item => item.name === 'description');
    const canonical = link.filter(item => item.rel === 'canonical');
    const hreflang = new Set(link.filter(item => item.rel === 'alternate').map(item => item.hreflang).filter(Boolean));
    const ogImage = meta.find(item => item.property === 'og:image')?.content;
    const ogImageAlt = meta.find(item => item.property === 'og:image:alt')?.content;
    const twitterImageAlt = meta.find(item => item.name === 'twitter:image:alt')?.content;
    pages.set(route, { isIndexable });
    if (isIndexable) indexable += 1; else noindex += 1;

    if ((html.match(/<main\b/gi) ?? []).length !== 1) errors.push(`${route}: must contain exactly one <main>`);
    if ((html.match(/<h1\b/gi) ?? []).length !== 1) errors.push(`${route}: must contain exactly one <h1>`);
    if (!title) errors.push(`${route}: missing title`);
    if (isIndexable && [...title].length > 70) errors.push(`${route}: title exceeds 70 characters`);
    if (description.length !== 1 || !description[0].content) errors.push(`${route}: missing or duplicate meta description`);
    if (description[0]?.content && [...description[0].content].length > 160) errors.push(`${route}: meta description exceeds 160 characters`);
    if (canonical.length !== 1) errors.push(`${route}: missing or duplicate canonical`);
    if (isIndexable && [...hreflang].sort().join(',') !== 'en,x-default,zh-CN') errors.push(`${route}: incomplete hreflang set`);
    if (!ogImage || !ogImageAlt || !twitterImageAlt) errors.push(`${route}: incomplete social image metadata`);
    if (ogImage?.endsWith('/logo-2.png')) errors.push(`${route}: legacy undersized social image`);

    if (isIndexable) {
      if (indexableTitles.has(title)) errors.push(`${route}: duplicate indexable title also used by ${indexableTitles.get(title)}`);
      else indexableTitles.set(title, route);
      if (canonical[0]?.href) {
        if (indexableCanonicals.has(canonical[0].href)) errors.push(`${route}: duplicate canonical also used by ${indexableCanonicals.get(canonical[0].href)}`);
        else indexableCanonicals.set(canonical[0].href, route);
      }
    }

    let previousHeading = 0;
    for (const match of html.matchAll(/<h([1-6])\b/gi)) {
      const level = Number(match[1]);
      if (previousHeading && level > previousHeading + 1) {
        errors.push(`${route}: heading jumps from h${previousHeading} to h${level}`);
        break;
      }
      previousHeading = level;
    }

    for (const script of html.matchAll(/<script\b[^>]*type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi)) {
      try { JSON.parse(decodeHtml(script[1])); } catch { errors.push(`${route}: invalid JSON-LD`); }
    }

    for (const anchor of tags(html, 'a')) {
      const href = anchor.href;
      if (!href || /^(?:#|mailto:|tel:|javascript:|data:)/i.test(href)) continue;
      let target;
      try { target = new URL(href, `${SITE_ORIGIN}${route}`); } catch { errors.push(`${route}: invalid href ${href}`); continue; }
      if (target.origin !== SITE_ORIGIN) continue;
      const normalized = target.pathname === '/' || target.pathname.endsWith('/') || extname(target.pathname)
        ? target.pathname
        : `${target.pathname}/`;
      if (redirects.has(target.pathname) || redirects.has(normalized)) errors.push(`${route}: internal link points through redirect ${href}`);
      else if (!targetExists(target.pathname, dist)) errors.push(`${route}: broken internal link ${href}`);
    }
  }

  const sitemapFiles = existsSync(resolve(dist, 'sitemaps')) ? walk(resolve(dist, 'sitemaps'), '.xml') : [];
  for (const file of sitemapFiles) {
    const xml = readFileSync(file, 'utf8');
    for (const match of xml.matchAll(/<loc>([^<]+)<\/loc>/g)) {
      const url = new URL(decodeHtml(match[1]));
      const page = pages.get(url.pathname);
      if (!page) errors.push(`${relative(dist, file)}: sitemap URL has no HTML page ${url.pathname}`);
      else if (!page.isIndexable) errors.push(`${relative(dist, file)}: sitemap includes noindex page ${url.pathname}`);
    }
  }

  for (const [source, destination] of redirects) {
    if (destination.startsWith('/') && !targetExists(destination, dist)) errors.push(`_redirects: ${source} points to missing ${destination}`);
  }

  return { passed: errors.length === 0, errors, pages: pages.size, indexable, noindex, redirects: redirects.size };
}

if (process.argv[1] && resolve(process.argv[1]) === new URL(import.meta.url).pathname) {
  const result = auditSeoOutput(resolve(import.meta.dirname, '..', 'dist'));
  process.stdout.write(`[seo-output] ${result.passed ? 'PASS' : 'FAIL'} ${JSON.stringify(result)}\n`);
  if (!result.passed) process.exitCode = 1;
}
