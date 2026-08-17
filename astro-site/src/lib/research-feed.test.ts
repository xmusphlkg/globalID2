import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildResearchFeedDefinitions,
  filterResearchFeedArticles,
  renderResearchFeedXml,
  type ResearchFeedData,
} from './research-feed.ts';

const data: ResearchFeedData = {
  last_updated: '2026-08-17T10:00:00Z',
  articles: [
    {
      slug: 'review-one',
      title: 'A systematic review of A&B',
      published_at: '2026-08-16T00:00:00Z',
      study_type: 'Systematic review',
      article_type: 'journal-article',
      peer_review_status: 'peer_reviewed',
      diseases: [{ disease_id: 'D028', slug: 'pertussis', name_en: 'Pertussis' }],
      countries: [{ code: 'CN', name_en: 'China' }],
      topics: [{ name: 'Vaccination' }],
      why_it_matters_en: 'Review & synthesis.',
    },
    {
      slug: 'model-main',
      title: 'Transmission model',
      published_at: '2026-08-17T00:00:00Z',
      study_type: 'Mathematical modelling',
      peer_review_status: 'peer_reviewed',
      diseases: [{ disease_id: 'D021', slug: 'dengue', name_en: 'Dengue' }],
      countries: [{ code: 'BR', name_en: 'Brazil' }],
      topics: [{ name: 'Transmission dynamics' }],
    },
  ],
  preprints: [
    {
      slug: 'model-two',
      title: 'Preprint transmission model',
      published_at: '2026-08-17T00:00:00Z',
      study_type: 'Mathematical modelling',
      peer_review_status: 'preprint',
      editorial_status: 'published',
    },
    {
      slug: 'held-preprint',
      title: 'Held preprint',
      peer_review_status: 'preprint',
      editorial_status: 'review',
    },
  ],
  reviews_and_guidelines: [{ slug: 'review-one' }],
  facets: {
    diseases: [
      { disease_id: 'D028', slug: 'pertussis', name_en: 'Pertussis', name_zh: '百日咳' },
      { disease_id: 'D021', slug: 'dengue', name_en: 'Dengue', name_zh: '登革热' },
    ],
    countries: [{ code: 'CN', slug: 'cn', name_en: 'China', name_zh: '中国' }],
    topics: [{ slug: 'vaccination', name: 'Vaccination' }],
  },
};

test('builds stable feed definitions for every supported filter family', () => {
  const definitions = buildResearchFeedDefinitions(data);
  const paths = new Set(definitions.map(definition => definition.path));
  for (const path of [
    '/research/rss/diseases/pertussis.xml',
    '/research/rss/countries/cn.xml',
    '/research/rss/topics/vaccination.xml',
    '/research/rss/study-types/mathematical-modelling.xml',
    '/research/rss/collections/reviews-and-guidelines.xml',
    '/research/rss/peer-review/peer-reviewed.xml',
    '/research/rss/peer-review/preprint.xml',
  ]) assert.equal(paths.has(path), true, path);

  assert.equal(definitions.find(definition => definition.path.endsWith('/preprint.xml'))?.count, 1);
});

test('filters diseases, countries, topics, study types, reviews, and peer-review status', () => {
  assert.deepEqual(filterResearchFeedArticles(data, 'diseases', 'pertussis').map(article => article.slug), ['review-one']);
  assert.deepEqual(filterResearchFeedArticles(data, 'countries', 'br').map(article => article.slug), ['model-main']);
  assert.deepEqual(filterResearchFeedArticles(data, 'topics', 'transmission-dynamics').map(article => article.slug), ['model-main']);
  assert.deepEqual(filterResearchFeedArticles(data, 'study-types', 'systematic-review').map(article => article.slug), ['review-one']);
  assert.deepEqual(filterResearchFeedArticles(data, 'collections', 'reviews-and-guidelines').map(article => article.slug), ['review-one']);
  assert.deepEqual(filterResearchFeedArticles(data, 'peer-review', 'peer-reviewed').map(article => article.slug), ['review-one', 'model-main']);
  assert.deepEqual(filterResearchFeedArticles(data, 'peer-review', 'preprint').map(article => article.slug), ['model-two']);
  assert.equal(filterResearchFeedArticles(data).some(article => article.slug === 'model-two'), false);
});

test('renders a self-identifying, escaped, newest-first filtered RSS document', () => {
  const definition = buildResearchFeedDefinitions(data).find(item => item.path === '/research/rss/topics/vaccination.xml');
  assert.ok(definition);
  const xml = renderResearchFeedXml({ data, definition, site: 'https://example.com' });
  assert.match(xml, /<atom:link href="https:\/\/example\.com\/research\/rss\/topics\/vaccination\.xml" rel="self"/);
  assert.match(xml, /<lastBuildDate>Mon, 17 Aug 2026 10:00:00 GMT<\/lastBuildDate>/);
  assert.match(xml, /A systematic review of A&amp;B/);
  assert.match(xml, /Review &amp; synthesis\./);
  assert.doesNotMatch(xml, /Transmission model/);
});
