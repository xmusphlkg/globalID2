import assert from 'node:assert/strict';
import { test } from 'node:test';
import { comparisonAreaCodes, comparisonAreaGroups, liveCompareDiseaseOptions } from './compareDiseaseOptions.ts';

test('comparison coverage follows live data instead of older download coverage', () => {
  const identities = [{ id: 'A', slug: 'a', en: 'A', zh: '甲', fr: 'A', countries: 90 }, { id: 'B', slug: 'b', en: 'B', zh: '乙', fr: 'B', countries: 0 }];
  const current = liveCompareDiseaseOptions(identities, { A: { country_count: 0 }, B: { country_count: 3 } });
  assert.deepEqual(current.map(row => [row.id, row.countries]), [['B', 3], ['A', 0]]);
  assert.deepEqual(liveCompareDiseaseOptions(identities).map(row => row.countries), [undefined, undefined]);
  assert.equal(identities[0].countries, 90);
  const withSeries = liveCompareDiseaseOptions(identities, { B: { country_count: 3 } }, { B: { country_series: { JP: { dates: ['2026-01-01'] } as any, 'JP-01': { dates: ['2026-01-01'] } as any, US: { dates: [] } as any } } });
  assert.equal(withSeries.find(row => row.id === 'B')?.countries, 1);
});

test('switching disease removes countries that have no current series', async () => {
  const { reconcileComparisonCountries } = await import('./compareDiseaseOptions.ts');
  assert.deepEqual(reconcileComparisonCountries(['AU', 'AT'], ['AU', 'JP']), { countries: ['AU'], removed: ['AT'] });
  assert.deepEqual(reconcileComparisonCountries(['AU', 'JP'], ['AU', 'JP']), { countries: ['AU', 'JP'], removed: [] });
});

test('missing observations remain explicit gaps rather than connecting adjacent dates', async () => {
  const { comparisonPoints } = await import('./compareDiseaseOptions.ts');
  assert.deepEqual(comparisonPoints(['2026-01-01', '2026-01-08', '2026-01-15', '2026-01-22'], [10, null, 0]), [
    ['2026-01-01', 10], ['2026-01-08', null], ['2026-01-15', 0], ['2026-01-22', null],
  ]);
});

test('comparison URL retains real subdivision keys, deduplicates and caps selections', () => {
  assert.deepEqual(comparisonAreaCodes('CN-BJ,JP,JP,US-CA,CA-ON,AU,../../invalid'), ['CN-BJ', 'JP', 'US-CA', 'CA-ON']);
  assert.deepEqual(comparisonAreaCodes(null), []);
});

test('comparison groups use published hierarchy and geography names without inventing parent series', () => {
  const series = { dates: ['2026-01-01'], name_en: 'Influenza', name_zh: '流感' } as any;
  const groups = comparisonAreaGroups(['D038'], { D038: { country_series: {
    'CN-BJ': series, 'CN-SH': series, 'CN-GD': { dates: [] } as any, HK: series, 'AU-NSW': series,
  } } }, [
    { code: 'CN', name_en: 'China', name_zh: '中国', name_fr: 'Chine' },
    { code: 'CN-BJ', name_en: 'Beijing', name_zh: '北京市', name_fr: 'Pékin', parent_code: null, source_info: { parent_country_code: 'CN' } },
    { code: 'CN-SH', name_en: 'Shanghai', name_zh: '上海市', parent_code: 'CN' },
    { code: 'CN-GD', name_en: 'Guangdong', parent_code: 'CN' },
    { code: 'HK', name_en: 'Hong Kong', name_zh: '香港' },
    { code: 'AU', name_en: 'Australia' },
    { code: 'AU-NSW', name_en: 'New South Wales' },
  ]);
  const cn = groups.find(group => group.area.code === 'CN')!;
  assert.equal(cn.area.selectable, false);
  assert.deepEqual(cn.children.map(area => area.code), ['CN-BJ', 'CN-SH']);
  assert.equal(cn.children[0].names.zh, '北京市');
  assert.equal(cn.children[0].names.fr, 'Pékin');
  assert.ok(cn.children.every(area => area.selectable));
  assert.equal(groups.find(group => group.area.code === 'HK')?.area.parentCode, null);
  assert.deepEqual(groups.find(group => group.area.code === 'AU')?.children.map(area => area.code), ['AU-NSW']);
});
