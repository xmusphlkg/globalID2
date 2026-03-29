import type { APIRoute } from 'astro';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import diseasesRaw from '../data/diseases/index.json';
import metaRaw from '../data/meta.json';
import reportsIndexRaw from '../data/reports/index.json';

type SiteEntry = {
  path: string;
  lastmod?: string | null;
};

type MetaCountry = {
  code: string;
};

type MetaData = {
  generated_at?: string;
  countries?: MetaCountry[];
};

type DiseaseSummary = {
  slug?: string;
};

type ReportSummary = {
  id: number | string;
  country_code?: string;
  created_at?: string;
};

const fallbackSite = 'https://globalinfectiousdisease.com';
const meta = metaRaw as MetaData;
const diseases = diseasesRaw as DiseaseSummary[];
const reportsIndex = reportsIndexRaw as ReportSummary[];

function normalizeDate(value?: string | null): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return undefined;
  return date.toISOString();
}

function escapeXml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&apos;');
}

function buildReportDiseaseEntries(report: ReportSummary): SiteEntry[] {
  const country = (report.country_code ?? 'cn').toLowerCase();
  const reportPath = resolve(`./src/data/reports/${report.id}.json`);
  const countryPath = resolve(`./src/data/countries/${country}.json`);

  if (!existsSync(reportPath) || !existsSync(countryPath)) return [];

  const reportData = JSON.parse(readFileSync(reportPath, 'utf-8')) as {
    sections?: Array<{ title?: string }>;
  };
  const countryData = JSON.parse(readFileSync(countryPath, 'utf-8')) as {
    disease_series?: Record<string, { name_en?: string; slug?: string }>;
  };

  const slugMap: Record<string, string> = {};
  for (const series of Object.values(countryData.disease_series ?? {})) {
    if (series.name_en && series.slug) {
      slugMap[series.name_en] = series.slug;
    }
  }

  const seen = new Set<string>();
  const entries: SiteEntry[] = [];

  for (const section of reportData.sections ?? []) {
    const diseaseName = (section.title ?? '').split(' - ').slice(0, -1).join(' - ');
    const slug = slugMap[diseaseName];
    if (!slug || seen.has(slug)) continue;

    seen.add(slug);
    entries.push({
      path: `/countries/${country}/reports/${report.id}/${slug}/`,
      lastmod: report.created_at,
    });
  }

  return entries;
}

function buildEntries(): SiteEntry[] {
  const siteLastmod = normalizeDate(meta.generated_at);
  const entries: SiteEntry[] = [
    { path: '/', lastmod: siteLastmod },
    { path: '/about/', lastmod: siteLastmod },
    { path: '/subscribe/', lastmod: siteLastmod },
    { path: '/countries/', lastmod: siteLastmod },
    { path: '/diseases/', lastmod: siteLastmod },
  ];

  for (const country of meta.countries ?? []) {
    const code = country.code.toLowerCase();
    entries.push(
      { path: `/countries/${code}/`, lastmod: siteLastmod },
      { path: `/countries/${code}/reports/`, lastmod: siteLastmod },
    );
  }

  for (const disease of diseases) {
    if (!disease.slug) continue;
    entries.push({
      path: `/diseases/${disease.slug}/`,
      lastmod: siteLastmod,
    });
  }

  for (const report of reportsIndex) {
    const country = (report.country_code ?? 'cn').toLowerCase();
    entries.push({
      path: `/countries/${country}/reports/${report.id}/`,
      lastmod: normalizeDate(report.created_at) ?? siteLastmod,
    });
    entries.push(...buildReportDiseaseEntries(report));
  }

  const deduped = new Map<string, SiteEntry>();
  for (const entry of entries) {
    if (!deduped.has(entry.path)) {
      deduped.set(entry.path, entry);
    }
  }

  return [...deduped.values()];
}

export const GET: APIRoute = ({ site }) => {
  const siteUrl = new URL(site?.toString() ?? fallbackSite);
  const urls = buildEntries();
  const xml = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls
    .map(({ path, lastmod }) => {
      const loc = new URL(path, siteUrl).toString();
      const lastmodTag = lastmod ? `\n    <lastmod>${escapeXml(lastmod)}</lastmod>` : '';
      return `  <url>\n    <loc>${escapeXml(loc)}</loc>${lastmodTag}\n  </url>`;
    })
    .join('\n')}\n</urlset>\n`;

  return new Response(xml, {
    headers: {
      'Content-Type': 'application/xml; charset=utf-8',
    },
  });
};
