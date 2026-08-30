import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import test from 'node:test';
import flagCatalogue from 'flag-icons/country.json' with { type: 'json' };
import {
  COUNTRY_COVERAGE,
  getCountryCoverage,
  hasCountryDataSnapshot,
  resolveCoverageStatus,
} from './country-coverage.ts';
import { getFlagAssetPath, resolveFlagIso2 } from './country-flag.ts';

test('an empty seeded metadata row does not activate a scheduled country', () => {
  const thailand = getCountryCoverage('TH');
  assert.ok(thailand);
  assert.equal(hasCountryDataSnapshot({}), false);
  assert.equal(resolveCoverageStatus(thailand, hasCountryDataSnapshot({})), 'Scheduled');
});

test('a snapshot requires records or a disease-bearing coverage window', () => {
  assert.equal(hasCountryDataSnapshot({ disease_count: 3 }), false);
  assert.equal(hasCountryDataSnapshot({ record_count: 2 }), true);
  assert.equal(hasCountryDataSnapshot({
    disease_count: 3,
    date_range: { start: '2024-01-01', end: '2024-12-31' },
  }), true);
});

test('existing active pipelines remain supported without a generated snapshot', () => {
  const unitedStates = getCountryCoverage('US');
  assert.ok(unitedStates);
  assert.equal(resolveCoverageStatus(unitedStates, false), 'Supported');
});

test('Ontario is a first-class region that activates when its snapshot exists', () => {
  const ontario = getCountryCoverage('ca-on');
  assert.ok(ontario);
  assert.equal(ontario.name_en, 'Ontario, Canada');
  assert.equal(ontario.cadence, 'Monthly');
  assert.equal(resolveCoverageStatus(ontario, false), 'Scheduled');
  assert.equal(resolveCoverageStatus(ontario, true), 'Supported');
});

test('Sweden remains supported after its verified baseline import', () => {
  const sweden = getCountryCoverage('SE');
  assert.ok(sweden);
  assert.equal(sweden.name_en, 'Sweden');
  assert.equal(sweden.cadence, 'Monthly');
  assert.equal(resolveCoverageStatus(sweden, false), 'Supported');
  assert.equal(resolveCoverageStatus(sweden, true), 'Supported');
});

test('ISO subdivision locations use their parent country flag', () => {
  assert.equal(resolveFlagIso2('CA-ON'), 'CA');
  assert.equal(resolveFlagIso2('ca-on'), 'CA');
  assert.equal(getFlagAssetPath('CA-ON'), '/flags/ca.svg');
});

test('every country coverage marker has a bundled flag asset', () => {
  for (const country of COUNTRY_COVERAGE) {
    const flag = new URL(`../../public${getFlagAssetPath(country.code)}`, import.meta.url);
    assert.equal(existsSync(flag), true, `${country.code} flag asset is missing`);
  }
});

test('the local flag library contains every ISO country flag', () => {
  const isoCountries = flagCatalogue.filter(country => country.iso && /^[a-z]{2}$/.test(country.code));
  assert.ok(isoCountries.length >= 249, `expected a complete ISO flag set, found ${isoCountries.length}`);

  for (const country of isoCountries) {
    const flag = new URL(`../../public/flags/${country.code}.svg`, import.meta.url);
    assert.equal(existsSync(flag), true, `${country.code.toUpperCase()} flag asset is missing`);
  }

  const license = new URL('../../public/flags/LICENSE.flag-icons.txt', import.meta.url);
  assert.equal(existsSync(license), true, 'flag-icons license is missing');
});
