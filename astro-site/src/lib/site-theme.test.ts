import assert from 'node:assert/strict';
import test from 'node:test';
import { themeStylesheetHref } from './site-theme.ts';

const entry = 'https://static.example.org/theme/compat/v1/theme.css';
test('theme covers localized home, country and comparison pages and is opt-in', () => {
  for (const page of ['/', '/zh/', '/fr/', '/countries/china/', '/zh/countries/china/', '/fr/countries/france/',
    '/compare/', '/zh/compare/', '/fr/compare/']) {
    assert.equal(themeStylesheetHref(page), null);
    assert.equal(themeStylesheetHref(page, entry), entry);
  }
  for (const page of ['/research/', '/research/articles/a/', '/countries/', '/countries/china/reports/1/', '/diseases/flu/',
    '/compare/unrelated/', '/comparison/']) {
    assert.equal(themeStylesheetHref(page, entry), null);
  }
});

test('preview can pin immutable theme while unsupported URLs fail the build', () => {
  const immutable = `/theme/versions/sha256/${'a'.repeat(64)}.css`;
  assert.equal(themeStylesheetHref('/', immutable), immutable);
  for (const url of ['http://static.example.org/theme/compat/v1/theme.css', '//static.example.org/theme/compat/v1/theme.css',
    'https://user:secret@static.example.org/theme/compat/v1/theme.css', `${entry}?v=latest`,
    'https://static.example.org/theme/compat/v2/theme.css', 'relative/theme/compat/v1/theme.css', '/theme\\compat/v1/theme.css']) {
    assert.throws(() => themeStylesheetHref('/', url), url);
  }
});
