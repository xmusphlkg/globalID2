import assert from 'node:assert/strict';
import { test } from 'node:test';
import { diseaseSearchAliases, matchesDiseaseSearch } from './disease-search.ts';
import { searchSiteEntries, type SiteSearchEntry } from './site-search.ts';

const influenza = { disease_id: 'D038', slug: 'influenza', name_en: 'Influenza', name_zh: '流行性感冒', name_fr: 'Grippe' };
const entry = (id: string, kind: string, en: string, zh: string, aliases: string[] = []): SiteSearchEntry => ({ id, kind, title: { en, zh }, href: { en: `/${id}/` }, aliases });
const entries = [
  entry('disease:D100', 'disease', 'Haemophilus influenzae', '流感嗜血杆菌'),
  entry('disease:D038', 'disease', 'Influenza', '流行性感冒', diseaseSearchAliases(influenza)),
  entry('country:jp', 'country', 'Japan', '日本', ['JP', 'Japon']),
];

test('ordinary disease aliases rank seasonal influenza above H. influenzae', () => {
  assert.equal(matchesDiseaseSearch(influenza, '流感'), true);
  assert.equal(matchesDiseaseSearch(influenza, 'flu'), true);
  assert.equal(matchesDiseaseSearch({ slug: 'haemophilus-influenzae', name_en: 'Haemophilus influenzae' }, 'seasonal influenza'), false);
  assert.equal(searchSiteEntries(entries, '流感', 'zh', 8).entries[0].id, 'disease:D038');
  assert.equal(searchSiteEntries(entries, 'flu', 'en', 8).entries[0].id, 'disease:D038');
});

test('combined country and disease query opens focused research and monitoring', () => {
  for (const query of ['日本流感', 'Japan influenza', 'flu in Japan', 'Japon grippe']) {
    const first = searchSiteEntries(entries, query, 'zh', 8).entries[0];
    assert.equal(first.id, 'focused:country:jp:disease:D038');
    assert.equal(first.href.zh, `/zh/research/ask/?q=${encodeURIComponent(query)}`);
  }
  assert.equal(searchSiteEntries(entries, 'virus influenza', 'en', 8).entries.some(item => item.id.startsWith('focused:')), false);
});

test('the full count is retained when a result preview is capped', () => {
  const result = searchSiteEntries(entries, '流感', 'zh', 1);
  assert.equal(result.entries.length, 1);
  assert.equal(result.total, 2);
});
