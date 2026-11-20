import { test } from 'node:test';
import assert from 'node:assert/strict';
import { localizedDiseaseName } from './diseaseNames.ts';

import {
  formatLocaleDate,
  formatLocaleNumber,
  localeTag,
  localizedRegionName,
  LOCALES,
} from './i18n.ts';

test('locale registry exposes stable BCP 47 tags', () => {
  assert.deepEqual(LOCALES.map((locale) => localeTag(locale.code)), ['en', 'fr', 'zh-CN']);
});

test('locale number formatting follows the selected language', () => {
  assert.equal(formatLocaleNumber(1234567.5, 'en'), '1,234,567.5');
  assert.equal(formatLocaleNumber(1234567.5, 'fr'), '1 234 567,5');
  assert.equal(formatLocaleNumber(1234567.5, 'zh'), '1,234,567.5');
  assert.equal(formatLocaleNumber(null, 'fr'), '—');
});

test('locale date formatting is UTC and returns a safe missing value', () => {
  const value = formatLocaleDate('2026-08-18T00:00:00Z', 'fr', {
    year: 'numeric', month: 'long', day: 'numeric', timeZone: 'UTC',
  });
  assert.equal(value, '18 août 2026');
  assert.equal(formatLocaleDate(undefined, 'fr'), '—');
});

test('French region labels fall back to the translated parent country', () => {
  assert.equal(localizedRegionName('fr', 'JP-23', { en: 'Aichi, Japan' }), 'Aichi, Japon');
  assert.equal(localizedRegionName('fr', 'US-CA', { en: 'California, United States' }), 'California, États-Unis');
});

test('French labels preserve the disease identity across full and compact datasets', () => {
  for (const record of [
    { disease_id: 'D168' },
    { slug: 'congenital-rubella-syndrome', name_en: 'Congenital rubella syndrome' },
  ]) {
    assert.equal(localizedDiseaseName(record, 'fr'), 'Syndrome de rubéole congénitale');
  }
  for (const record of [
    { disease_id: 'D236' },
    { slug: 'methicillin-resistant-staphylococcus-aureus-(mrsa)-surveillance', name_en: 'MRSA surveillance' },
  ]) {
    assert.equal(localizedDiseaseName(record, 'fr'), 'Surveillance de Staphylococcus aureus résistant à la méthicilline (SARM)');
  }
});
