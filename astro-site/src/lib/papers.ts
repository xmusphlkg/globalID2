/**
 * Long-tail research pages (article pages, social cards and the research
 * site-data shards) are served from a separate host backed by Cloudflare R2 so
 * the main Pages project stays under its file-count ceiling. Everything that
 * emits a URL to those paths goes through this module so canonical URLs,
 * sitemaps, feeds and in-page links agree on the host.
 */

export const PAPERS_ORIGIN = 'https://papers.globalinfectiousdisease.com';
export const REPORTS_ORIGIN = 'https://reports.globalinfectiousdisease.com';

const PAPERS_PATH = /^\/(?:(?:zh|fr)\/)?research\/(?:articles|diseases)\/|^\/research\/catalogue\/(?:diseases|studies)\/|^\/research\/cards\/|^\/site-data\/research\//;
const REPORTS_PATH = /^\/(?:(?:zh|fr)\/)?reports(?:\/|$)|^\/(?:(?:zh|fr)\/)?countries\/[^/]+\/reports(?:\/|$)|^\/site-data\/reports\//;

type Locale = 'en' | 'zh' | 'fr';

const localePrefix = (locale: Locale) => (locale === 'zh' ? '/zh' : locale === 'fr' ? '/fr' : '');

/** True when a root-relative path is published on the papers host. */
export function isPapersPath(path: string): boolean {
  return PAPERS_PATH.test(path);
}

/** True when a root-relative path is published on the reports host. */
export function isReportsPath(path: string): boolean {
  return REPORTS_PATH.test(path);
}

/**
 * Root-relative path of an article page. Use this where a path (not a URL) is
 * required, for example canonical paths handed to the layout.
 */
export function articlePath(slug: string, locale: Locale = 'en'): string {
  return `${localePrefix(locale)}/research/articles/${slug}/`;
}

/**
 * Absolute URL of an article page on the papers host. Local development keeps
 * the root-relative path because the dev server renders articles itself.
 */
export function articleHref(slug: string, locale: Locale = 'en'): string {
  return siteHref(articlePath(slug, locale));
}

/**
 * Href for a site path that works from any host: papers paths become absolute
 * papers URLs, everything else is returned unchanged. Local development keeps
 * root-relative paths because the dev server renders articles itself.
 */
export function siteHref(path: string): string {
  const dev = (import.meta as { env?: { DEV?: boolean } }).env?.DEV === true;
  if (dev) return path;
  if (isReportsPath(path)) return `${REPORTS_ORIGIN}${path}`;
  return isPapersPath(path) ? `${PAPERS_ORIGIN}${path}` : path;
}

export const MAIN_ORIGIN = 'https://globalinfectiousdisease.com';

/**
 * Base for runtime fetches of main-site files (release.json, search index).
 * Pages served from the papers host cannot use root-relative URLs for them.
 */
export function mainSiteBase(): string {
  const host = typeof location === 'undefined' ? '' : location.hostname;
  return [PAPERS_ORIGIN, REPORTS_ORIGIN].some(origin => host === new URL(origin).hostname)
    ? MAIN_ORIGIN
    : '';
}

/** Resolve a site path to the host that owns the path. */
export function absoluteSiteUrl(path: string, siteBase: string | URL): URL {
  const base = isReportsPath(path)
    ? REPORTS_ORIGIN
    : isPapersPath(path)
      ? PAPERS_ORIGIN
      : siteBase;
  return new URL(path, base);
}
