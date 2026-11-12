import test from 'node:test';
import assert from 'node:assert/strict';
import { researchSearchEntries } from './research-search-entries.ts';

test('research search includes current indexable articles and excludes hidden records', () => {
  const entries = researchSearchEntries({ articles: [
    { article_id: 'a', slug: 'a', title: 'Current paper', doi: '10/example', diseases: [{ name_en: 'Dengue' }] },
    { article_id: 'hidden', slug: 'hidden', title: 'Hidden', indexable: false },
  ], preprints: [{ article_id: 'p', slug: 'p', title: 'Preprint' }] });
  assert.deepEqual(entries.map(entry => entry.id), ['research:a', 'research:p']);
  assert.ok(entries[0].aliases.includes('Dengue'));
  assert.equal(entries[0].href.en, 'https://papers.globalinfectiousdisease.com/research/articles/a/');
});
