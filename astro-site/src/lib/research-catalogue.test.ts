import assert from 'node:assert/strict';
import test from 'node:test';
import { compactResearchArticle } from './research-catalogue.ts';

test('compact catalogue retains every field used by client-side research filters', () => {
  const compact = compactResearchArticle({
    article_id: 'a1', slug: 'a1', title: 'Study', publisher: 'Publisher',
    authors: ['First Author', 'Second Author'],
    pathogens: [{ id: 'p1', name: 'Pathogen' }],
    pathogen_types: [{ id: 'virus', name: 'Virus' }],
    populations: [{ id: 'pregnant_women', name: 'Pregnant women' }],
    diseases: [], countries: [], topics: [],
    related_surveillance: [{ url: '/diseases/example/', disease_name_en: 'Example' }],
  });

  assert.equal(compact.publisher, 'Publisher');
  assert.deepEqual(compact.authors, ['First Author', 'Second Author']);
  assert.deepEqual(compact.pathogens, [{ id: 'p1', name: 'Pathogen' }]);
  assert.deepEqual(compact.pathogen_types, [{ id: 'virus', name: 'Virus' }]);
  assert.deepEqual(compact.populations, [{ id: 'pregnant_women', name: 'Pregnant women' }]);
  assert.deepEqual(compact.related_surveillance, [{ url: '/diseases/example/', disease_name_en: 'Example' }]);
});
