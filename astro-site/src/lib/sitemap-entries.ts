import {
  hasCountryDataSnapshot,
  type CountryDataSnapshotMeta,
} from './country-coverage.ts';
import {
  buildPublishableReports,
  normalizeReportRouteSegment,
  reportArchiveCountries,
  type ReportIndexEntry,
} from './report-routes.ts';

export interface SitemapEntry {
  path: string;
  lastmod?: string;
}

export interface SitemapMeta {
  generated_at?: unknown;
  countries?: Array<CountryDataSnapshotMeta & { code?: unknown }>;
}

export interface SitemapDisease {
  slug?: unknown;
}

export interface SitemapReport extends ReportIndexEntry {
  id?: unknown;
  created_at?: unknown;
}

export interface BuildSitemapEntriesOptions {
  meta: SitemapMeta;
  diseases: unknown;
  reports: unknown;
  loadReport: (id: string) => unknown | null;
}

export const STATIC_SITEMAP_PATHS = [
  '/',
  '/about/',
  '/changelog/',
  '/subscribe/',
  '/terms/',
  '/countries/',
  '/diseases/',
] as const;

export function normalizeSitemapDate(value: unknown): string | undefined {
  if (typeof value !== 'string' && typeof value !== 'number' && !(value instanceof Date)) {
    return undefined;
  }

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return undefined;
  return date.toISOString();
}

function pathSegment(value: unknown, lowercase = false): string | null {
  return normalizeReportRouteSegment(value, lowercase);
}

export function buildSitemapEntries({
  meta,
  diseases,
  reports,
  loadReport,
}: BuildSitemapEntriesOptions): SitemapEntry[] {
  const siteLastmod = normalizeSitemapDate(meta.generated_at);
  const entries: SitemapEntry[] = STATIC_SITEMAP_PATHS.map(path => ({
    path,
    lastmod: siteLastmod,
  }));

  for (const country of meta.countries ?? []) {
    const code = pathSegment(country.code, true);
    if (!code || !hasCountryDataSnapshot(country)) continue;

    entries.push({
      path: `/countries/${code}/`,
      lastmod: siteLastmod,
    });
  }

  if (Array.isArray(diseases)) {
    for (const disease of diseases as SitemapDisease[]) {
      const slug = pathSegment(disease?.slug);
      if (!slug) continue;

      entries.push({
        path: `/diseases/${slug}/`,
        lastmod: siteLastmod,
      });
    }
  }

  const publishableReports = buildPublishableReports(reports, loadReport);

  for (const country of reportArchiveCountries(publishableReports)) {
    entries.push({
      path: `/countries/${country}/reports/`,
      lastmod: siteLastmod,
    });
  }

  for (const report of publishableReports) {
    const lastmod = normalizeSitemapDate(report.summary.created_at)
      ?? normalizeSitemapDate(report.detail.created_at)
      ?? siteLastmod;

    entries.push({
      path: `/countries/${report.country}/reports/${report.id}/`,
      lastmod,
    });

    for (const slug of report.diseaseSlugs) {
      entries.push({
        path: `/countries/${report.country}/reports/${report.id}/${slug}/`,
        lastmod,
      });
    }
  }

  const deduped = new Map<string, SitemapEntry>();
  for (const entry of entries) {
    if (!deduped.has(entry.path)) deduped.set(entry.path, entry);
  }
  return [...deduped.values()];
}

function escapeXml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&apos;');
}

export function renderSitemapXml(entries: SitemapEntry[], site: URL | string): string {
  const siteUrl = site instanceof URL ? site : new URL(site);
  const body = entries.map(({ path, lastmod }) => {
    const loc = new URL(path, siteUrl).toString();
    const lastmodTag = lastmod ? `\n    <lastmod>${escapeXml(lastmod)}</lastmod>` : '';
    return `  <url>\n    <loc>${escapeXml(loc)}</loc>${lastmodTag}\n  </url>`;
  }).join('\n');

  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${body}\n</urlset>\n`;
}
