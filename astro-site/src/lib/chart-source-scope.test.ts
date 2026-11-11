import test from 'node:test';
import assert from 'node:assert/strict';
import { chartSourceScope, scopedChartSources } from './chart-source-scope.ts';
import { adaptCatalog } from '../components/charts/surveillance/adapters/catalog.ts';
import { analyze } from '../components/charts/surveillance/analysis/engine.ts';
import { initialConfig } from '../components/charts/surveillance/state/config.ts';

test('selected Japanese curves never inherit the first Australian source in worldwide metadata', () => {
  const info = [
    { country_code: 'AU', sources: [{ label: 'Australia NINDSS', url: 'https://health.gov.au/' }] },
    { country_code: 'JP', sources: [{ label: 'Japan NIID weekly', url: 'https://id-info.jihs.go.jp/' }] },
  ];
  assert.deepEqual(scopedChartSources(info, ['JP']), info[1].sources);
  assert.deepEqual(scopedChartSources(info, []), []);
  assert.deepEqual(scopedChartSources(info, ['CN']), []);
});

test('multiple selected countries retain their own authorities and deduplicate provenance', () => {
  const source = { label: 'Japan NIID', url: 'https://example.test/jp' };
  const info = [{ country_code: 'JP', sources: [source] }, { country_code: 'CN', sources: [{ label: 'China NHC' }] }];
  assert.deepEqual(scopedChartSources(info, ['JP', 'CN'], [source]), [source, { label: 'China NHC' }]);
  assert.deepEqual(scopedChartSources({ sources: [{ label: 'Authority', url: 'javascript:alert(1)' }] }, ['JP']), [{ label: 'Authority' }]);
});

test('scope follows the selected entity and visible nonmissing observations rather than global coverage', () => {
  const entry = (name: string, dates: string[], cases: Array<number | null>) => ({ disease_id: 'D038', name_en: name, name_zh: name, dates, cases, deaths: [], incidence_rates: [], weekly_equiv_cases: [], total_cases: 12, period_granularity: 'monthly' });
  const catalog = adaptCatalog({ AU: entry('Australia', ['1924-01-01'], [2]), JP: entry('Japan', ['2026-01-01', '2026-02-01', '2026-03-01'], [0, 12, null]) }, { id: 'research:D038', kind: 'disease', diseaseId: 'D038' });
  const config = { ...initialConfig(catalog, 'trend', ['JP::published']), window: { startDate: '2026-02-01', endDate: '2026-03-01' } };
  const scope = chartSourceScope(catalog, analyze(catalog, config));
  assert.deepEqual(scope.geographies, ['JP']);
  assert.deepEqual(scope.dates, ['2026-02-01']);
  assert.deepEqual(scope.provenance, []);
});
