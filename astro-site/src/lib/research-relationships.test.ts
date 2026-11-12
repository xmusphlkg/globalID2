import { test } from 'node:test';
import assert from 'node:assert/strict';
import { researchRelationships, researchTermLabel } from './research-relationships.ts';

test('relationships expose distinct translated target entities and useful links', () => {
  const rows = researchRelationships({ nodes: [
    { id: 'd:1', type: 'disease', label: 'Dengue', name_zh: '登革热', url: '/diseases/dengue/' },
    { id: 'd:2', type: 'disease', label: 'Chikungunya', name_zh: '基孔肯雅热' },
    { id: 'c:fr', type: 'country', label: 'France', code: 'FR', url: '/countries/fr/' },
    { id: 't:1', type: 'topic', label: 'Transmission dynamics' },
  ], edges: [
    { target: 'd:1', relation: 'ABOUT_DISEASE' }, { target: 'd:2', relation: 'ABOUT_DISEASE' },
    { target: 'c:fr', relation: 'STUDIED_IN' }, { target: 't:1', relation: 'ADDRESSES_TOPIC' },
    { target: 'missing', relation: 'ABOUT_DISEASE' },
  ] }, 'zh');
  assert.deepEqual(rows.map(row => row.target), ['登革热', '基孔肯雅热', '法国', '传播动力学']);
  assert.equal(rows[0].relation, '疾病');
  assert.equal(rows[0].href, '/diseases/dengue/');
  assert.equal(rows[2].href, '/research/countries/fr/');
  assert.equal(rows[3].href, '/research/topics/transmission-dynamics/');
  assert.equal(researchTermLabel('Mathematical modelling', 'zh'), '数学建模');
  assert.equal(researchTermLabel('Dengue virus', 'zh'), 'Dengue virus');
});
