import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  contentLanguageForPreference,
  formatLocaleDate,
  formatLocaleNumber,
  localeTag,
  LOCALES,
  LOCALE_VARIANTS,
  normalizeLocalePreference,
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

test('regional French preference reuses the reviewed French route without losing intent', () => {
  const canada = LOCALE_VARIANTS.find((locale) => locale.id === 'fr-CA');
  assert.ok(canada);
  assert.equal(canada.bcp47, 'fr-CA');
  assert.equal(canada.pathPrefix, '/fr');
  assert.equal(canada.contentLanguage, 'fr');
  assert.equal(contentLanguageForPreference('fr-CA'), 'fr');
  assert.equal(normalizeLocalePreference('fr_ca'), 'fr-CA');
});
