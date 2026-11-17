import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createCountryPageNavigation } from './country-page-navigation.ts';

test('country navigation preserves report order, the six-report limit, total counts and localized routes', () => {
  const reports = Array.from({ length: 9 }, (_, id) => ({ id, country_code: id === 2 ? 'CN' : 'JP' }));
  const navigation = createCountryPageNavigation(reports, [{ code: 'jp', slug: 'influenza' }, { code: 'cn', slug: 'cholera' }]);
  for (const prefix of ['', '/zh', '/fr']) {
    const actual = navigation('jp', prefix);
    assert.deepEqual(actual.reports, reports.filter(row => row.country_code === 'JP').slice(0, 6));
    assert.equal(actual.reportCount, 8);
    assert.deepEqual(actual.diseaseRoutes, { influenza: `${prefix}/countries/jp/diseases/influenza/` });
  }
  assert.deepEqual(navigation('unknown'), { reports: [], reportCount: 0, diseaseRoutes: {} });
});

test('generation consumes each catalogue once even when countries and locales grow', () => {
  let reportReads = 0, routeReads = 0;
  const reports = { *[Symbol.iterator]() { for (let i = 0; i < 10_000; i++) { reportReads++; yield { id: i, country_code: `C${i % 500}` }; } } };
  const routes = { *[Symbol.iterator]() { for (let i = 0; i < 5_000; i++) { routeReads++; yield { code: `c${i % 500}`, slug: `d${i}` }; } } };
  const navigation = createCountryPageNavigation(reports, routes);
  for (const prefix of ['', '/zh', '/fr']) for (let i = 0; i < 500; i++) assert.equal(navigation(`c${i}`, prefix).reportCount, 20);
  assert.equal(reportReads, 10_000);
  assert.equal(routeReads, 5_000);
});
