import assert from 'node:assert/strict';
import test from 'node:test';
import { availableSubdivisionParents, geographyScopeIds, subdivisionParent } from './curveGeography.ts';
import type { DiseaseDatasetSeriesEntry } from './diseaseDataset.ts';

const entry: DiseaseDatasetSeriesEntry = {
  disease_id: 'D021', name_en: 'Dengue', name_zh: '登革热',
  dates: ['2025-01-01'], cases: [10], deaths: [0],
  weekly_equiv_cases: [], incidence_rates: [null], total_cases: 10,
};
const series = Object.fromEntries(['CN', 'CN-GD', 'CN-ZJ', 'AU', 'AU-NSW', 'BR'].map((id) => [id, entry]));

test('province comparison includes subdivisions without their national aggregate', () => {
  assert.deepEqual(geographyScopeIds(series, 'subdivisions:CN'), ['CN-GD', 'CN-ZJ']);
  assert.deepEqual(geographyScopeIds(series, 'subdivisions:AU'), ['AU-NSW']);
  assert.deepEqual(geographyScopeIds(series, 'national'), ['CN', 'AU', 'BR']);
});

test('mixed comparison retains countries and subdivisions and respects restricted entities', () => {
  assert.deepEqual(geographyScopeIds(series, 'mixed'), Object.keys(series));
  assert.deepEqual(geographyScopeIds(series, 'mixed', ['CN-GD', 'BR', 'unknown']), ['CN-GD', 'BR']);
  assert.deepEqual(availableSubdivisionParents(series), ['AU', 'CN']);
  assert.deepEqual(availableSubdivisionParents(series, ['CN-GD', 'BR']), ['CN']);
  assert.equal(subdivisionParent('cn-gd'), 'CN');
  assert.equal(subdivisionParent('BR'), null);
});
