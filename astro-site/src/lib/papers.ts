/**
 * Long-tail research pages (article pages, social cards and the research
 * site-data shards) are served from a separate host backed by Cloudflare R2 so
 * the main Pages project stays under its file-count ceiling. Everything that
 * emits a URL to those paths goes through this module so canonical URLs,
 * sitemaps, feeds and in-page links agree on the host.
 */

export const PAPERS_ORIGIN = 'https://papers.globalinfectiousdisease.com';

const PAPERS_PATH = /^\/(?:(?:zh|fr)\/)?research\/articles\/|^\/research\/cards\/|^\/site-data\/research\//;

type Locale = 'en' | 'zh' | 'fr';

const localePrefix = (locale: Locale) => (locale === 'zh' ? '/zh' : locale === 'fr' ? '/fr' : '');

/** True when a root-relative path is published on the papers host. */
export function isPapersPath(path: string): boolean {
  return PAPERS_PATH.test(path);
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
  return !dev && isPapersPath(path) ? `${PAPERS_ORIGIN}${path}` : path;
}

export const MAIN_ORIGIN = 'https://globalinfectiousdisease.com';

/**
 * Base for runtime fetches of main-site files (release.json, search index).
 * Pages served from the papers host cannot use root-relative URLs for them.
 */
export function mainSiteBase(): string {
  const host = typeof location === 'undefined' ? '' : location.hostname;
  return host === new URL(PAPERS_ORIGIN).hostname ? MAIN_ORIGIN : '';
}

/** Resolve a site path to an absolute URL, sending papers paths to their host. */
export function absoluteSiteUrl(path: string, siteBase: string | URL): URL {
  return new URL(path, isPapersPath(path) ? PAPERS_ORIGIN : siteBase);
}
