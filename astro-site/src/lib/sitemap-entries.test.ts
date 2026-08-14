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
import { toSeoSlug } from './seo.ts';

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
  const localizedStaticPaths = STATIC_SITEMAP_PATHS.flatMap(path => path === '/' ? ['/', '/zh/'] : [path, `/zh${path}`]);
  const localized = (path: string) => [path, `/zh${path}`];
  assert.deepEqual([...byPath.keys()], [
    ...localizedStaticPaths,
    ...localized('/diseases/influenza/'),
    ...localized('/countries/jp/'),
    ...localized('/countries/jp/reports/'),
    ...localized('/countries/jp/reports/10/'),
    ...localized('/countries/jp/reports/10/influenza/'),
    ...localized('/countries/jp/reports/10/measles/'),
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

test('publishes latest, methodology, weekly and monthly situation URLs only after the switch', () => {
  const base = {
    meta: { generated_at: '2026-08-13T00:00:00Z', countries: [] },
    diseases: [], reports: [], loadReport: () => null,
  };
  const hidden = buildSitemapEntries({
    ...base,
    situation: { latest: { public_enabled: false, content_updated_at: '2026-08-13T02:00:00Z' } },
  });
  assert.equal(hidden.some(entry => entry.path.startsWith('/situation/')), false);

  const visible = buildSitemapEntries({
    ...base,
    situation: {
      latest: { public_enabled: true, content_updated_at: '2026-08-13T02:00:00Z' },
      weeks: [{ period_key: '2026-W33', content_updated_at: '2026-08-13T02:00:00Z' }],
      months: [{ period_key: '2026-08', content_updated_at: '2026-08-13T02:00:00Z' }],
    },
  });
  const paths = new Set(visible.map(entry => entry.path));
  for (const path of ['/situation/', '/situation/methodology/', '/situation/2026-W33/', '/situation/2026-08/']) {
    assert.equal(paths.has(path), true);
    assert.equal(paths.has(`/zh${path}`), true);
  }
  assert.equal(visible.find(entry => entry.path === '/situation/2026-08/')?.lastmod, '2026-08-13T02:00:00.000Z');
});

test('publishes Research Radar articles, collections, weekly briefs, and RSS', () => {
  const entries = buildSitemapEntries({
    meta: { generated_at: '2026-08-13T00:00:00Z', countries: [] },
    diseases: [],
    reports: [],
    loadReport: () => null,
    research: {
      last_updated: '2026-08-13T01:00:00Z',
      articles: [{ slug: 'dengue-study', updated_at: '2026-08-12T00:00:00Z' }],
      facets: {
        diseases: [{ slug: 'dengue' }],
        countries: [{ slug: 'br' }],
        topics: [{ slug: 'vaccination' }],
        weeks: [{ week: '2026-W33' }, { week: 'bad-week' }],
      },
    },
  });
  const paths = new Set(entries.map(entry => entry.path));
  for (const path of [
    '/research/',
    '/research/rss.xml',
    '/research/articles/dengue-study/',
    '/research/diseases/dengue/',
    '/research/countries/br/',
    '/research/topics/vaccination/',
    '/research/weekly/2026-W33/',
  ]) assert.equal(paths.has(path), true);
  assert.equal(paths.has('/research/weekly/bad-week/'), false);
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

  const expectedPaths = new Set<string>(STATIC_SITEMAP_PATHS.flatMap(path => path === '/' ? ['/', '/zh/'] : [path, `/zh${path}`]));
  for (const country of meta.countries ?? []) {
    const code = normalizeReportRouteSegment(country.code, true);
    if (!code || !hasCountryDataSnapshot(country)) continue;
    expectedPaths.add(`/countries/${code}/`); expectedPaths.add(`/zh/countries/${code}/`);
  }
  for (const disease of diseases) {
    const slug = toSeoSlug(disease.slug);
    if (slug) { expectedPaths.add(`/diseases/${slug}/`); expectedPaths.add(`/zh/diseases/${slug}/`); }
  }

  const publishableReports = buildPublishableReports(
    reports,
    id => details.get(id) ?? null,
  );
  for (const country of reportArchiveCountries(publishableReports)) {
    expectedPaths.add(`/countries/${country}/reports/`); expectedPaths.add(`/zh/countries/${country}/reports/`);
  }
  for (const report of publishableReports) {
    expectedPaths.add(`/countries/${report.country}/reports/${report.id}/`); expectedPaths.add(`/zh/countries/${report.country}/reports/${report.id}/`);
    for (const slug of report.diseaseSlugs) {
      expectedPaths.add(`/countries/${report.country}/reports/${report.id}/${slug}/`); expectedPaths.add(`/zh/countries/${report.country}/reports/${report.id}/${slug}/`);
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
