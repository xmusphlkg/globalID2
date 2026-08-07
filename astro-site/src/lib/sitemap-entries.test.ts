import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { hasCountryDataSnapshot } from './country-coverage.ts';
import {
  buildPublishableReports,
  normalizeReportRouteSegment,
  reportArchiveCountries,
} from './report-routes.ts';
import {
  STATIC_SITEMAP_PATHS,
  buildSitemapEntries,
  renderSitemapXml,
  type SitemapMeta,
  type SitemapReport,
} from './sitemap-entries.ts';

test('builds only publishable static, country, disease, and report URLs', () => {
  const details = new Map<string, unknown>([
    ['10', {
      id: 10,
      country_code: 'JP',
      created_at: '2026-08-05',
      report_document_v4: {
        disease_directory: [
          { slug: 'influenza' },
          { slug: 'measles' },
          { slug: 'influenza' },
          { slug: '' },
          { slug: 'bad/slug' },
        ],
      },
    }],
    ['11', {
      id: 11,
      country_code: 'CA',
      schema_version: 'report_v4.0',
    }],
  ]);

  const entries = buildSitemapEntries({
    meta: {
      generated_at: '2026-08-07T12:00:00Z',
      countries: [
        { code: 'JP', data_available: true },
        { code: 'US', data_available: false, record_count: 0, disease_count: 0 },
      ],
    },
    diseases: [{ slug: 'influenza' }, { slug: '' }, {}],
    reports: [
      {
        id: 10,
        country_code: 'JP',
        created_at: 'not-a-date',
        metadata: { report_layout: 'report_v4' },
      },
      {
        id: 11,
        country_code: 'US',
        schema_version: 'report_v4.0',
      },
      {
        id: 12,
        country_code: 'CN',
        metadata: { report_layout: 'report_v3' },
      },
    ],
    loadReport: id => details.get(id) ?? null,
  });

  const byPath = new Map(entries.map(entry => [entry.path, entry]));
  assert.deepEqual([...byPath.keys()], [
    ...STATIC_SITEMAP_PATHS,
    '/countries/jp/',
    '/diseases/influenza/',
    '/countries/jp/reports/',
    '/countries/jp/reports/10/',
    '/countries/jp/reports/10/influenza/',
    '/countries/jp/reports/10/measles/',
  ]);
  assert.equal(byPath.has('/countries/us/'), false);
  assert.equal(byPath.has('/countries/us/reports/'), false);
  assert.equal(byPath.get('/countries/jp/reports/10/')?.lastmod, '2026-08-05T00:00:00.000Z');
});

test('renders absolute XML URLs and escapes XML-sensitive characters', () => {
  const xml = renderSitemapXml([
    { path: '/terms/', lastmod: '2026-08-07T00:00:00.000Z' },
    { path: '/diseases/a&b/' },
  ], 'https://example.com');

  assert.match(xml, /<loc>https:\/\/example\.com\/terms\/<\/loc>/);
  assert.match(xml, /<lastmod>2026-08-07T00:00:00\.000Z<\/lastmod>/);
  assert.match(xml, /<loc>https:\/\/example\.com\/diseases\/a&amp;b\/<\/loc>/);
});

test('current generated data matches the Astro static-route intent', () => {
  const dataDirectory = fileURLToPath(new URL('../data/', import.meta.url));
  const readJson = (relativePath: string): any => JSON.parse(
    readFileSync(`${dataDirectory}/${relativePath}`, 'utf-8'),
  );

  const meta = readJson('meta.json') as SitemapMeta;
  const diseases = readJson('diseases/index.json') as Array<{ slug?: string }>;
  const reports = readJson('reports/index.json') as SitemapReport[];
  const details = new Map<string, any>();

  for (const report of reports) {
    const id = normalizeReportRouteSegment(report.id);
    if (!id) continue;
    const detailPath = `${dataDirectory}/reports/${id}.json`;
    if (!existsSync(detailPath)) continue;
    details.set(id, readJson(`reports/${id}.json`));
  }

  const entries = buildSitemapEntries({
    meta,
    diseases,
    reports,
    loadReport: id => details.get(id) ?? null,
  });
  const actualPaths = new Set(entries.map(entry => entry.path));

  const expectedPaths = new Set<string>(STATIC_SITEMAP_PATHS);
  for (const country of meta.countries ?? []) {
    const code = normalizeReportRouteSegment(country.code, true);
    if (!code || !hasCountryDataSnapshot(country)) continue;
    expectedPaths.add(`/countries/${code}/`);
  }
  for (const disease of diseases) {
    const slug = normalizeReportRouteSegment(disease.slug);
    if (slug) expectedPaths.add(`/diseases/${slug}/`);
  }

  const publishableReports = buildPublishableReports(
    reports,
    id => details.get(id) ?? null,
  );
  for (const country of reportArchiveCountries(publishableReports)) {
    expectedPaths.add(`/countries/${country}/reports/`);
  }
  for (const report of publishableReports) {
    expectedPaths.add(`/countries/${report.country}/reports/${report.id}/`);
    for (const slug of report.diseaseSlugs) {
      expectedPaths.add(`/countries/${report.country}/reports/${report.id}/${slug}/`);
    }
  }

  assert.equal(entries.length, actualPaths.size, 'sitemap paths must be unique');
  assert.deepEqual([...actualPaths].sort(), [...expectedPaths].sort());
  assert.equal(actualPaths.has('/terms/'), true);

  for (const country of meta.countries ?? []) {
    if (typeof country.code !== 'string' || hasCountryDataSnapshot(country)) continue;
    assert.equal(actualPaths.has(`/countries/${country.code.toLowerCase()}/`), false);
  }
});
