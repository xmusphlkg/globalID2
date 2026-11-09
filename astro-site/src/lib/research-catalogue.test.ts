import assert from 'node:assert/strict';
import test from 'node:test';
import { compactResearchArticle } from './research-catalogue.ts';

test('compact catalogue retains every field used by client-side research filters', () => {
  const compact = compactResearchArticle({
    article_id: 'a1', slug: 'a1', title: 'Study', publisher: 'Publisher',
    publication_week: '2026-W37',
    authors: ['First Author', 'Second Author'],
    pathogens: [{ id: 'p1', name: 'Pathogen' }],
    pathogen_types: [{ id: 'virus', name: 'Virus' }],
    populations: [{ id: 'pregnant_women', name: 'Pregnant women' }],
    diseases: [], countries: [], topics: [],
    related_surveillance: [{ url: '/diseases/example/', disease_name_en: 'Example' }],
  });

  assert.equal(compact.publisher, 'Publisher');
  assert.equal(compact.publication_week, '2026-W37');
  assert.deepEqual(compact.authors, ['First Author', 'Second Author']);
  assert.deepEqual(compact.pathogens, [{ id: 'p1', name: 'Pathogen' }]);
  assert.deepEqual(compact.pathogen_types, [{ id: 'virus', name: 'Virus' }]);
  assert.deepEqual(compact.populations, [{ id: 'pregnant_women', name: 'Pregnant women' }]);
  assert.deepEqual(compact.related_surveillance, [{ url: '/diseases/example/', disease_name_en: 'Example' }]);
});

test('bibliographic records retain source identifiers and do not claim an evidence page', () => {
  const compact = compactResearchArticle({
    article_id: 'bibliographic', title: 'Study', doi: '10.1000/example', pmid: '123',
    detail_available: false, content_tier: 'bibliographic',
  });
  assert.equal(compact.detail_available, false);
  assert.equal(compact.content_tier, 'bibliographic');
  assert.equal(compact.doi, '10.1000/example');
  assert.equal(compact.pmid, '123');
});
