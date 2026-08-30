import assert from 'node:assert/strict';
import test from 'node:test';

import { COUNTRY_COVERAGE } from './country-coverage.ts';
import {
  COUNTRY_CLASSIFICATION_METADATA,
  getCountryClassification,
} from './country-classifications.ts';

test('every country coverage entry has geographic and income classifications', () => {
  const missing = COUNTRY_COVERAGE
    .filter(country => !getCountryClassification(country.code))
    .map(country => country.code);

  assert.deepEqual(missing, []);
});

test('subnational coverage inherits its parent country classification', () => {
  assert.deepEqual(getCountryClassification('CA-ON'), getCountryClassification('CA'));
});

test('classification snapshot records its official source vintage', () => {
  assert.equal(COUNTRY_CLASSIFICATION_METADATA.worldBankFiscalYear, 2027);
  assert.match(COUNTRY_CLASSIFICATION_METADATA.sources.who_regions, /^https:\/\/apps\.who\.int\//);
  assert.match(COUNTRY_CLASSIFICATION_METADATA.sources.world_bank_groups, /^https:\/\/datahelpdesk\.worldbank\.org\//);
});
