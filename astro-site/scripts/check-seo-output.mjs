import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { extname, join, relative, resolve } from 'node:path';

const SITE_ORIGIN = 'https://globalinfectiousdisease.com';
// Mirrors PAPERS_PATH in src/lib/papers.ts: paths published on the papers host.
const PAPERS_ROUTE = /^\/(?:(?:zh|fr)\/)?research\/(?:articles|diseases)\/|^\/research\/catalogue\/diseases\/|^\/research\/cards\/|^\/site-data\/research\//;

// Regex captures can be slices backed by an entire HTML document. The global
// uniqueness maps must own small strings, otherwise one canonical per page
// keeps the whole catalogue's HTML alive until the audit ends.
const ownedString = value => Buffer.from(value, 'utf8').toString('utf8');

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

export function tags(html, name) {
  const tagPattern = new RegExp(`<${name}\\b(?:[^>"']|"[^"]*"|'[^']*')*>`, 'gi');
  return [...html.matchAll(tagPattern)].map(match => attributes(match[0]));
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

// Destination of the first rule matching `pathname`: an exact rule, or a
// trailing-wildcard rule such as `/research/articles/*` (papers host split).
export function redirectFor(pathname, redirects) {
  if (redirects.has(pathname)) return redirects.get(pathname);
  for (const [source, destination] of redirects) {
    if (source.endsWith('*') && pathname.startsWith(source.slice(0, -1))) return destination;
  }
  return undefined;
}

function isRedirectSource(route, redirects) {
  return redirectFor(route, redirects) !== undefined;
}

export function auditSeoOutput(distDirectory, { files, partial = false, catalogue } = {}) {
  const dist = resolve(distDirectory);
  const redirects = redirectMap(dist);
  // Shared navigation repeats across thousands of pages. Cache only within
  // this audit so a later audit still observes changed files and redirects.
  const targets = new Map();
  const redirectSources = new Map();
  const hasTarget = pathname => {
    if (!targets.has(pathname)) targets.set(pathname, targetExists(pathname, dist));
    return targets.get(pathname);
  };
  const hasRedirect = pathname => {
    if (!redirectSources.has(pathname)) redirectSources.set(pathname, isRedirectSource(pathname, redirects));
    return redirectSources.get(pathname);
  };
  const errors = [...(catalogue?.errors ?? [])];
  const pages = new Map(catalogue?.pages);
  const indexableTitles = new Map();
  const indexableCanonicals = new Map();
  let indexable = catalogue?.indexable ?? 0;
  let noindex = catalogue?.noindex ?? 0;

  for (const file of files ?? walk(dist, '.html')) {
    const route = routeFor(file, dist);
    // Exact rules only: a wildcard rule (papers host) must not hide pages that
    // an unsplit build still contains from the audit.
    if (redirects.has(route)) continue;
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

    // Astro emits a small HTML redirect document for `Astro.redirect()` routes
    // when building a static site. It intentionally has no document structure
    // or social metadata; the redirect target is what needs auditing instead.
    const isGeneratedRedirect = /<meta\b[^>]*http-equiv=["']?refresh\b/i.test(html);
    if (isGeneratedRedirect) continue;

    if ((html.match(/<main\b/gi) ?? []).length !== 1) errors.push(`${route}: must contain exactly one <main>`);
    if ((html.match(/<h1\b/gi) ?? []).length !== 1) errors.push(`${route}: must contain exactly one <h1>`);
    if (!title) errors.push(`${route}: missing title`);
    if (isIndexable && [...title].length > 70) errors.push(`${route}: title exceeds 70 characters`);
    if (description.length !== 1 || !description[0].content) errors.push(`${route}: missing or duplicate meta description`);
    if (description[0]?.content && [...description[0].content].length > 160) errors.push(`${route}: meta description exceeds 160 characters`);
    if (canonical.length !== 1) errors.push(`${route}: missing or duplicate canonical`);
    if (isIndexable && [...hreflang].sort().join(',') !== 'en,fr-FR,x-default,zh-CN') errors.push(`${route}: incomplete hreflang set`);
    if (!ogImage || !ogImageAlt || !twitterImageAlt) errors.push(`${route}: incomplete social image metadata`);
    if (ogImage?.endsWith('/logo-2.png')) errors.push(`${route}: legacy undersized social image`);

    if (isIndexable) {
      // Localized routes are expected to reuse the same subject title in each
      // language. Detect collisions within a locale, not across translations.
      const locale = route.startsWith('/fr/') ? 'fr' : route.startsWith('/zh/') ? 'zh' : 'en';
      const titleKey = ownedString(`${locale}:${title}`);
      if (indexableTitles.has(titleKey)) errors.push(`${route}: duplicate indexable title also used by ${indexableTitles.get(titleKey)}`);
      else indexableTitles.set(titleKey, route);
      if (canonical[0]?.href) {
        if (indexableCanonicals.has(canonical[0].href)) errors.push(`${route}: duplicate canonical also used by ${indexableCanonicals.get(canonical[0].href)}`);
        else indexableCanonicals.set(ownedString(canonical[0].href), route);
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
      // Papers pages and the paths they link to share one host after the
      // release split, so those links never pass through a redirect.
      const sameHost = PAPERS_ROUTE.test(route) && PAPERS_ROUTE.test(target.pathname);
      if (!sameHost && (hasRedirect(target.pathname) || hasRedirect(normalized))) errors.push(`${route}: internal link points through redirect ${href}`);
      else if (!hasTarget(target.pathname)) errors.push(`${route}: broken internal link ${href}`);
    }
  }

  if (partial) return { errors, pages: [...pages], indexable, noindex,
    titles: [...indexableTitles], canonicals: [...indexableCanonicals] };

  const sitemapFiles = existsSync(resolve(dist, 'sitemaps')) ? walk(resolve(dist, 'sitemaps'), '.xml') : [];
  for (const file of sitemapFiles) {
    const xml = readFileSync(file, 'utf8');
    for (const match of xml.matchAll(/<loc>([^<]+)<\/loc>/g)) {
      const url = new URL(decodeHtml(match[1]));
      // URLs on another host (the papers site) have no page in this dist.
      if (url.origin !== SITE_ORIGIN) continue;
      const page = pages.get(url.pathname);
      if (!page && hasRedirect(url.pathname)) errors.push(`${relative(dist, file)}: sitemap URL redirects to another host ${url.pathname}`);
      else if (!page) errors.push(`${relative(dist, file)}: sitemap URL has no HTML page ${url.pathname}`);
      else if (!page.isIndexable) errors.push(`${relative(dist, file)}: sitemap includes noindex page ${url.pathname}`);
    }
  }

  for (const [source, destination] of redirects) {
    if (destination.startsWith('/') && !destination.includes(':splat') && !hasTarget(destination)) errors.push(`_redirects: ${source} points to missing ${destination}`);
  }

  return { passed: errors.length === 0, errors, pages: pages.size, indexable, noindex, redirects: redirects.size };
}

if (process.argv[1] && resolve(process.argv[1]) === new URL(import.meta.url).pathname) {
  const result = auditSeoOutput(resolve(import.meta.dirname, '..', 'dist'));
  process.stdout.write(`[seo-output] ${result.passed ? 'PASS' : 'FAIL'} ${JSON.stringify(result)}\n`);
  if (!result.passed) process.exitCode = 1;
}
