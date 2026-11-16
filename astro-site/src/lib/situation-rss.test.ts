import assert from 'node:assert/strict';
import { test } from 'node:test';
import { buildSituationRssOptions } from './situation-rss.ts';

test('RSS selectors offer only Situation entities with published events, using localized names', () => {
  const mapping = { facets: {
    countries: [{ id: 'AU', count: 2, country_name_en: 'Australia', country_name_zh: '澳大利亚' },
      { id: 'US', count: 0, country_name_en: 'United States' }, { id: '../secret', count: 3 }],
    diseases: [{ id: 'D014', count: 2 }, { id: 'D050', count: 0 }, { id: 'D999', count: 3 }],
  } };
  const catalogue = [{ disease_id: 'D014', name_en: 'Avian influenza', name_zh: '禽流感', name_fr: 'Grippe aviaire' },
    { disease_id: 'D050', name_en: 'Ebola', name_zh: '埃博拉', name_fr: 'Ebola' }];
  const chinese = buildSituationRssOptions(mapping, catalogue, 'zh');
  assert.deepEqual(chinese, { countries: [{ code: 'AU', name: '澳大利亚' }], diseases: [{ id: 'D014', name: '禽流感' }] });
  assert.equal(buildSituationRssOptions(mapping, catalogue, 'fr').diseases[0].name, 'Grippe aviaire');
  assert.deepEqual(buildSituationRssOptions(null, catalogue, 'en'), { countries: [], diseases: [] });
});
