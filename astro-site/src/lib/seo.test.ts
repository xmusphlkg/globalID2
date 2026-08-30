import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildBreadcrumbStructuredData,
  buildCountryMetaDescription,
  buildDatasetDistributions,
  buildDiseaseMetaDescription,
  buildAlternatePaths,
  buildSeoTitle,
  clampSeoDescription,
  clampSeoTitle,
  formatYearRange,
  isIndexableDisease,
  isIndexableResearchCollection,
  toSeoSlug,
} from './seo.ts';

test('builds unique, descriptive country and disease snippets', () => {
  const country = buildCountryMetaDescription({
    name: 'Japan',
    diseaseCount: 12,
    start: '2000-01-01',
    end: '2026-08-01',
  });
  const disease = buildDiseaseMetaDescription({
    name: 'Plague',
    category: 'Bacterial',
    countryCount: 7,
    start: '2000-01-01',
    end: '2026-08-01',
  });

  assert.match(country, /^Japan surveillance data/);
  assert.match(country, /12 tracked infectious diseases/);
  assert.match(disease, /^Plague \(bacterial\) surveillance data/);
  assert.match(disease, /7 countries/);
  assert.ok(country.length >= 100);
  assert.ok(disease.length >= 100);
});

test('builds absolute breadcrumb URLs including the current page', () => {
  const data = buildBreadcrumbStructuredData({
    siteOrigin: 'https://example.com',
    currentPath: '/countries/jp/',
    breadcrumbs: [
      { label: 'Countries', href: '/countries/' },
      { label: 'Japan' },
    ],
  });

  const items = data?.itemListElement as Array<Record<string, unknown>>;
  assert.deepEqual(items.map(item => item.item), [
    'https://example.com/',
    'https://example.com/countries/',
    'https://example.com/countries/jp/',
  ]);
});

test('exposes page JSON and only the current download part', () => {
  const distributions = buildDatasetDistributions({
    siteOrigin: 'https://example.com',
    fallbackSiteJsonPath: '/site-data/diseases/d001.json',
    entry: {
      parts: [
        { files: { csv: { filename: 'old.csv', url: 'https://data.example/old.csv' } } },
        {
          is_current: true,
          files: {
            csv: { bytes: 42, filename: 'current.csv', url: 'https://data.example/current.csv' },
            json: { filename: 'missing.json' },
          },
        },
      ],
    },
  });

  assert.equal(distributions.length, 2);
  assert.equal(distributions[0].contentUrl, 'https://example.com/site-data/diseases/d001.json');
  assert.equal(distributions[1].contentUrl, 'https://data.example/current.csv');
  assert.equal(distributions[1].encodingFormat, 'text/csv');
  assert.equal(distributions[1].contentSize, '42 bytes');
});

test('normalizes SEO slugs, titles, descriptions, and language alternates', () => {
  assert.equal(toSeoSlug('Respiratory syncytial virus infection (RSV)'), 'respiratory-syncytial-virus-infection-rsv');
  assert.equal(formatYearRange('1982-01-01', '2026-08-01'), '1982–2026');
  assert.equal(
    buildSeoTitle('Pertussis', 'surveillance data', '1982–2026'),
    'Pertussis surveillance data 1982–2026 | GIDS',
  );
  assert.equal(buildAlternatePaths('/countries/jp/', 'en').zh, '/zh/countries/jp/');
  assert.equal(buildAlternatePaths('/zh/countries/jp/', 'zh').en, '/countries/jp/');
  assert.ok(clampSeoDescription('word '.repeat(60)).length <= 160);
  assert.ok(clampSeoTitle('A deliberately long evidence summary title '.repeat(4)).length <= 70);
  assert.equal(isIndexableResearchCollection(1), false);
  assert.equal(isIndexableResearchCollection(2), true);
});

test('requires an observation or sourced published profile before indexing a disease', () => {
  assert.equal(isIndexableDisease({ countrySeries: { jp: { dates: ['2026-01-01'] } } }), true);
  assert.equal(isIndexableDisease({ knowledgeStatus: 'published', knowledgeSources: [{ url: 'https://example.gov/source' }] }), true);
  assert.equal(isIndexableDisease({ knowledgeStatus: 'draft', knowledgeSources: [{ url: 'https://example.gov/source' }] }), false);
});
