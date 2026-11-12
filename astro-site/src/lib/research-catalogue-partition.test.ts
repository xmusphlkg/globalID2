import { test } from 'node:test';
import assert from 'node:assert/strict';
import { partitionResearchCatalogue } from './research-catalogue-partition.ts';

test('study partitions preserve every record once and avoid unrelated network reads', () => {
  const records = Array.from({length: 1001}, (_, id) => ({id, study_type: id % 10 === 0 ? 'Model' : 'Cohort'}));
  const chunks = partitionResearchCatalogue(records);
  assert.deepEqual(chunks.flat().sort((a,b) => a.id-b.id), records);
  assert.ok(chunks.every(chunk => chunk.length <= 100 && new Set(chunk.map(x=>x.study_type)).size === 1));
  assert.equal(chunks.filter(chunk => chunk[0].study_type === 'Model').length, 2);
  assert.deepEqual(partitionResearchCatalogue([]), []);
  assert.throws(() => partitionResearchCatalogue(records, 0));
});

test('reviewed and bibliographic records never share a chunk or lose records', () => {
  const records = Array.from({length: 1000}, (_, id) => ({id, study_type: 'Cohort', detail_available: id % 10 === 0}));
  const chunks = partitionResearchCatalogue(records);
  assert.deepEqual(chunks.flat().sort((a,b) => a.id-b.id), records);
  assert.ok(chunks.every(chunk => new Set(chunk.map(x=>x.detail_available)).size === 1));
  assert.equal(chunks.filter(chunk => chunk[0].detail_available).length, 1);
});
