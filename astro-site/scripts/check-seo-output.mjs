import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { extname, join, relative, resolve } from 'node:path';

const SITE_ORIGIN = 'https://globalinfectiousdisease.com';
// Exact public endpoint implemented by surveillance-data's Situation feed
// handler. Do not exempt the broad Worker route prefix: typos must still fail.
export const WORKER_DYNAMIC_LINK_PATHS = Object.freeze(['/site-data/situation/rss.xml']);
const workerDynamicLinks = new Set(WORKER_DYNAMIC_LINK_PATHS);
export const isIndependentResearchRoute = pathname => /^\/(?:(?:zh|fr)\/)?research(?:\/|$)/.test(pathname);
// Mirrors PAPERS_PATH in src/lib/papers.ts: paths published on the papers host.
const PAPERS_ROUTE = /^\/(?:(?:zh|fr)\/)?research\/(?:articles|diseases|countries|weekly)\/|^\/research\/catalogue\/diseases\/|^\/research\/cards\/|^\/site-data\/research\//;

// Regex captures can be slices backed by an entire HTML document. The global
// uniqueness maps must own small strings, otherwise one canonical per page
// keeps the whole catalogue's HTML alive until the audit ends.
const ownedString = value => Buffer.from(value, 'utf8').toString('utf8');

const NAMED_ENTITIES = Object.freeze({ amp: '&', apos: "'", gt: '>', lt: '<', quot: '"' });
const HTML_ENTITY_RE = /&(#x[\da-f]+|#\d+|amp|apos|gt|lt|quot);/gi;
const ATTR_RE = /([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
const TAG_PATTERN_CACHE = new Map();
const TITLE_RE = /<title>([\s\S]*?)<\/title>/i;
const META_OR_LINK_TAG_RE = /<(meta|link)\b(?:[^>"']|"[^"]*"|'[^']*')*>/gi;
const MAIN_OR_HEADING_OPEN_RE = /<(?:(main)|h([1-6]))\b/gi;
const JSON_LD_SCRIPT_RE = /<script\b[^>]*type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi;
const SKIP_HREF_RE = /^(?:#|mailto:|tel:|javascript:|data:)/i;
const REFRESH_VALUE_RE = /^refresh\b/i;
const WHITESPACE_RE = /\s+/g;
const HREF_HINT_RE = /\bhref\b/i;
const ANCHOR_TAG_RE = /<a\b(?:[^>"']|"[^"]*"|'[^']*')*>/gi;

function tagPattern(name) {
  let pattern = TAG_PATTERN_CACHE.get(name);
  if (!pattern) {
    pattern = new RegExp(`<${name}\\b(?:[^>"']|"[^"]*"|'[^']*')*>`, 'gi');
    TAG_PATTERN_CACHE.set(name, pattern);
  }
  return pattern;
}

function walk(directory, suffix) {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? walk(path, suffix) : entry.name.endsWith(suffix) ? [path] : [];
  });
}

function decodeHtml(value) {
  const text = String(value ?? '');
  if (!text.includes('&')) return text;
  return text.replace(HTML_ENTITY_RE, (match, entity) => {
    if (entity[0] !== '#') return NAMED_ENTITIES[entity.toLowerCase()] ?? match;
    const hexadecimal = entity[1] === 'x' || entity[1] === 'X';
    return String.fromCodePoint(Number.parseInt(entity.slice(hexadecimal ? 2 : 1), hexadecimal ? 16 : 10));
  });
}

export function attributes(tag) {
  const output = {};
  for (const match of tag.matchAll(ATTR_RE)) {
    output[match[1].toLowerCase()] = decodeHtml(match[2] ?? match[3] ?? match[4] ?? '');
  }
  return output;
}

export function tags(html, name) {
  const output = [];
  for (const match of html.matchAll(tagPattern(name))) {
    output.push(attributes(match[0]));
  }
  return output;
}

function extractAnchorHref(rawTag) {
  if (!rawTag.includes('href') && !HREF_HINT_RE.test(rawTag)) return undefined;
  let href;
  ATTR_RE.lastIndex = 0;
  for (let match; (match = ATTR_RE.exec(rawTag)) !== null;) {
    const key = match[1];
    if (key.length === 4 && (key === 'href' || key.toLowerCase() === 'href')) {
      href = decodeHtml(match[2] ?? match[3] ?? match[4] ?? '');
    }
  }
  return href;
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

export function auditSeoOutput(distDirectory, { files, partial = false, catalogue, independentResearch = process.env.GIDS_RESEARCH_PUBLICATION_ENABLED === '1' } = {}) {
  const dist = resolve(distDirectory);
  const redirects = redirectMap(dist);
  const wildcardRedirects = [];
  for (const [source, destination] of redirects) {
    if (source.endsWith('*')) wildcardRedirects.push([source.slice(0, -1), destination]);
  }
  // Shared navigation repeats across thousands of pages. Cache only within
  // this audit so a later audit still observes changed files and redirects.
  const targets = new Map();
  const redirectSources = new Map();
  const parsedHrefs = new Map();
  const hasTarget = pathname => {
    if (independentResearch && isIndependentResearchRoute(pathname)) return true;
    if (pathname === '/site-data/research/graph.json') {
      // Papers registers this logical key against the generated graph at publication.
      return independentResearch || targetExists('/site-data/research-graph.json', dist);
    }
    if (workerDynamicLinks.has(pathname)) return true;
    if (!targets.has(pathname)) targets.set(ownedString(pathname), targetExists(pathname, dist));
    return targets.get(pathname);
  };
  const hasRedirect = pathname => {
    if (independentResearch && isIndependentResearchRoute(pathname)) return false;
    if (!redirectSources.has(pathname)) {
      let matched = redirects.has(pathname);
      if (!matched) {
        for (let i = 0; i < wildcardRedirects.length; i++) {
          if (pathname.startsWith(wildcardRedirects[i][0])) { matched = true; break; }
        }
      }
      redirectSources.set(ownedString(pathname), matched);
    }
    return redirectSources.get(pathname);
  };
  const resolveLinkTarget = (href, route) => {
    const cacheable = (href.startsWith('/') && !href.startsWith('//'))
      || href.startsWith('https://')
      || href.startsWith('http://');
    if (cacheable) {
      const cached = parsedHrefs.get(href);
      if (cached !== undefined) return cached;
      let entry;
      try {
        const target = new URL(href, SITE_ORIGIN);
        if (target.origin !== SITE_ORIGIN) {
          entry = { external: true };
        } else {
          const pathname = ownedString(target.pathname);
          const normalized = pathname === '/' || pathname.endsWith('/') || extname(pathname)
            ? pathname
            : `${pathname}/`;
          entry = { external: false, pathname, normalized, isPapersTarget: PAPERS_ROUTE.test(pathname) };
        }
      } catch {
        entry = null;
      }
      if (parsedHrefs.size < 20_000 && href.length < 256) parsedHrefs.set(ownedString(href), entry);
      return entry;
    }
    try {
      const target = new URL(href, `${SITE_ORIGIN}${route}`);
      if (target.origin !== SITE_ORIGIN) return { external: true };
      const pathname = target.pathname;
      const normalized = pathname === '/' || pathname.endsWith('/') || extname(pathname)
        ? pathname
        : `${pathname}/`;
      return { external: false, pathname, normalized, isPapersTarget: PAPERS_ROUTE.test(pathname) };
    } catch {
      return null;
    }
  };
  const errors = [...(catalogue?.errors ?? [])];
  const pages = new Map(catalogue?.pages);
  const indexableTitles = new Map();
  const indexableCanonicals = new Map();
  let indexable = catalogue?.indexable ?? 0;
  let noindex = catalogue?.noindex ?? 0;

  for (const file of files ?? walk(dist, '.html')) {
    const route = routeFor(file, dist);
    if (independentResearch && isIndependentResearchRoute(route)) continue;
    // Exact rules only: a wildcard rule (papers host) must not hide pages that
    // an unsplit build still contains from the audit.
    if (redirects.has(route)) continue;
    const html = readFileSync(file, 'utf8');
    const title = decodeHtml(html.match(TITLE_RE)?.[1] ?? '').replace(WHITESPACE_RE, ' ').trim();
    let robots = '';
    const description = [];
    const canonical = [];
    const hreflang = new Set();
    let ogImage;
    let ogImageAlt;
    let twitterImageAlt;
    let isGeneratedRedirect = false;
    for (const tagMatch of html.matchAll(META_OR_LINK_TAG_RE)) {
      const item = attributes(tagMatch[0]);
      if (tagMatch[1][0] === 'm' || tagMatch[1][0] === 'M') {
        if (!isGeneratedRedirect && item['http-equiv'] && REFRESH_VALUE_RE.test(item['http-equiv'])) {
          isGeneratedRedirect = true;
        }
        if (item.name === 'robots' && !robots && item.content) robots = item.content.toLowerCase();
        if (item.name === 'description') description.push(item);
        if (item.property === 'og:image' && ogImage === undefined) ogImage = item.content;
        if (item.property === 'og:image:alt' && ogImageAlt === undefined) ogImageAlt = item.content;
        if (item.name === 'twitter:image:alt' && twitterImageAlt === undefined) twitterImageAlt = item.content;
      } else {
        if (item.rel === 'canonical') canonical.push(item);
        else if (item.rel === 'alternate' && item.hreflang) hreflang.add(item.hreflang);
      }
    }
    const isIndexable = !robots.includes('noindex');
    pages.set(route, { isIndexable });
    if (isIndexable) indexable += 1; else noindex += 1;

    // Astro emits a small HTML redirect document for `Astro.redirect()` routes
    // when building a static site. It intentionally has no document structure
    // or social metadata; the redirect target is what needs auditing instead.
    if (isGeneratedRedirect) continue;

    let mainCount = 0;
    let h1Count = 0;
    let previousHeading = 0;
    let headingJump = null;
    for (const match of html.matchAll(MAIN_OR_HEADING_OPEN_RE)) {
      if (match[1] !== undefined) {
        mainCount += 1;
      } else {
        const level = match[2].charCodeAt(0) - 48;
        if (level === 1) h1Count += 1;
        if (!headingJump && previousHeading && level > previousHeading + 1) {
          headingJump = `${route}: heading jumps from h${previousHeading} to h${level}`;
        }
        previousHeading = level;
      }
    }

    if (mainCount !== 1) errors.push(`${route}: must contain exactly one <main>`);
    if (h1Count !== 1) errors.push(`${route}: must contain exactly one <h1>`);
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

    if (headingJump) errors.push(headingJump);

    for (const script of html.matchAll(JSON_LD_SCRIPT_RE)) {
      try { JSON.parse(decodeHtml(script[1])); } catch { errors.push(`${route}: invalid JSON-LD`); }
    }

    const routeIsPapers = PAPERS_ROUTE.test(route);
    for (const anchorMatch of html.matchAll(ANCHOR_TAG_RE)) {
      const href = extractAnchorHref(anchorMatch[0]);
      if (!href || SKIP_HREF_RE.test(href)) continue;
      const target = resolveLinkTarget(href, route);
      if (target === null) { errors.push(`${route}: invalid href ${href}`); continue; }
      if (target.external) continue;
      // Papers pages and the paths they link to share one host after the
      // release split, so those links never pass through a redirect.
      const sameHost = routeIsPapers && target.isPapersTarget;
      if (!sameHost && (hasRedirect(target.pathname) || hasRedirect(target.normalized))) errors.push(`${route}: internal link points through redirect ${href}`);
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
      if (independentResearch && isIndependentResearchRoute(url.pathname)) continue;
      const page = pages.get(url.pathname);
      if (!page && hasRedirect(url.pathname)) errors.push(`${relative(dist, file)}: sitemap URL redirects to another host ${url.pathname}`);
      else if (!page) errors.push(`${relative(dist, file)}: sitemap URL has no HTML page ${url.pathname}`);
      else if (!page.isIndexable) errors.push(`${relative(dist, file)}: sitemap includes noindex page ${url.pathname}`);
    }
  }

  for (const [source, destination] of redirects) {
    if (independentResearch && isIndependentResearchRoute(source)) continue;
    if (destination.startsWith('/') && !destination.includes(':splat') && !hasTarget(destination)) errors.push(`_redirects: ${source} points to missing ${destination}`);
  }

  return { passed: errors.length === 0, errors, pages: pages.size, indexable, noindex, redirects: redirects.size };
}

if (process.argv[1] && resolve(process.argv[1]) === new URL(import.meta.url).pathname) {
  const result = auditSeoOutput(resolve(import.meta.dirname, '..', 'dist'));
  process.stdout.write(`[seo-output] ${result.passed ? 'PASS' : 'FAIL'} ${JSON.stringify(result)}\n`);
  if (!result.passed) process.exitCode = 1;
}
