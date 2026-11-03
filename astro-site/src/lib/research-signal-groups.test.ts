import assert from 'node:assert/strict';
import test from 'node:test';
import { groupResearchSignals } from './research-signal-groups.ts';

test('repeated countries and duplicate source events collapse without losing distinct events', () => {
  const signal = { disease_id: 'D002', relation_level: 'disease_context', signal_id: 'one', geographies: [{ code: 'AO', name_en: 'Angola' }], data_through: '2026-10-01' };
  const groups = groupResearchSignals([signal, signal, { ...signal, signal_id: 'two', geographies: [{ code: 'ao', name_en: 'Angola' }, { code: 'BI', name_en: 'Burundi' }], data_through: '2026-10-27' }]);
  assert.equal(groups.length, 1);
  assert.deepEqual([...groups[0].places.keys()], ['AO', 'BI']);
  assert.equal(groups[0].signals.length, 2);
  assert.equal(groups[0].latest, '2026-10-27');
});

test('exact, historical and disease-only relationships and different diseases stay separate', () => {
  const base = { disease_id: 'D002', geographies: [{ code: 'AO' }] };
  const groups = groupResearchSignals([
    { ...base, relation_level: 'disease_context' },
    { ...base, relation_level: 'historical_disease_geography_context' },
    { ...base, relation_level: 'exact_disease_geography' },
    { ...base, disease_id: 'D038', relation_level: 'disease_context' },
  ]);
  assert.equal(groups.length, 4);
  assert.deepEqual(groups.map(g => g.relation), ['exact_disease_geography', 'historical_disease_geography_context', 'disease_context', 'disease_context']);
});
