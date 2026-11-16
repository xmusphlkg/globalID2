import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import type { MapPoint, MonitoringProduct, SiteIndex } from '../../utils/situationTypes.ts';
import { filterMonitoring } from './monitoring.ts';
import { DEFAULT_FILTERS } from './model.ts';

test('a report without per-event host metadata cannot match an explicit host filter', () => {
  assert.deepEqual(filterMonitoring([product('a')], { ...DEFAULT_FILTERS, host: 'animal' }, AS_OF, site, []), []);
  assert.deepEqual(filterMonitoring([product('a')], { ...DEFAULT_FILTERS, host: 'unknown' }, AS_OF, site, []), []);
});

const AS_OF = '2026-11-15';
const site: SiteIndex = {
  countries: { SA: { name_en: 'Saudi Arabia', name_zh: '沙特阿拉伯', seriesCount: 0, researchCount: 0 } },
  diseases: { D049: { name_en: 'MERS', name_zh: '中东呼吸综合征', slug: 'mers', researchCount: 0 } }, series: {},
};
const points = [{ disease_id: 'D065', disease_name_en: 'Monkeypox', disease_name_zh: '猴痘', disease_label: 'Mpox', country_code: 'SA', country_name_en: 'Saudi Arabia' }] as MapPoint[];
const product = (id: string, extra: Partial<MonitoringProduct> = {}): MonitoringProduct => ({
  notice_id: id, source_id: 'who', source_label_en: 'WHO', source_label_zh: null,
  agency_en: null, agency_zh: null, title_en: 'MERS update in Saudi Arabia', url: `https://example.org/${id}`,
  published_date: '2026-11-10', alert_count: 0, ...extra,
});

test('monitoring combines source and time filters, rejects future/invalid dates and deduplicates notices', () => {
  const rows = [product('a'), product('a'), product('old', { published_date: '2026-10-01' }),
    product('other', { source_id: 'ecdc' }), product('future', { published_date: '2026-11-16' }),
    product('invalid', { published_date: 'bad' })];
  assert.deepEqual(filterMonitoring(rows, { ...DEFAULT_FILTERS, source: 'who' }, AS_OF, site, points).map(row => row.notice_id), ['a']);
  assert.deepEqual(filterMonitoring(rows, { ...DEFAULT_FILTERS, source: 'who', window: 'all' }, AS_OF, site, points).map(row => row.notice_id), ['a', 'old']);
});

test('legacy title coverage combines disease and area without substring false positives', () => {
  const rows = [product('a'), product('false', { title_en: 'Customers in Saudi Arabia' }),
    product('unknown', { title_en: 'Weekly epidemiological roundup' }), product('elsewhere', { title_en: 'MERS in Qatar' })];
  assert.deepEqual(filterMonitoring(rows, { ...DEFAULT_FILTERS, disease: 'D049', country: 'SA' }, AS_OF, site, points).map(row => row.notice_id), ['a']);
});

test('legacy title matching accepts aliases from points, slug separators and Chinese names', () => {
  const rows = [product('mpox', { title_en: 'Mpox in Saudi Arabia' }), product('zh', { title_en: '沙特阿拉伯中东呼吸综合征监测报告' })];
  assert.deepEqual(filterMonitoring(rows, { ...DEFAULT_FILTERS, disease: 'D065' }, AS_OF, site, points).map(row => row.notice_id), ['mpox']);
  assert.deepEqual(filterMonitoring(rows, { ...DEFAULT_FILTERS, disease: 'D049', country: 'SA' }, AS_OF, site, points).map(row => row.notice_id), ['zh']);
  const slugSite = { ...site, diseases: { D049: { ...site.diseases.D049, name_en: 'Other label', slug: 'middle-east-respiratory-syndrome' } } };
  assert.equal(filterMonitoring([product('slug', { title_en: 'Middle East respiratory syndrome update' })], { ...DEFAULT_FILTERS, disease: 'D049' }, AS_OF, slugSite, []).length, 1);
});

test('explicit coverage takes precedence over title and supports reports with generic titles', () => {
  const rows = [product('explicit', { title_en: 'Weekly roundup', disease_ids: ['D049'], country_codes: ['SA'] }),
    product('not-covered', { disease_ids: ['D065'], country_codes: ['SA'] }),
    product('empty', { disease_ids: [], country_codes: [] })];
  assert.deepEqual(filterMonitoring(rows, { ...DEFAULT_FILTERS, disease: 'D049', country: 'SA' }, AS_OF, site, points).map(row => row.notice_id), ['explicit']);
});

test('unknown disease or area metadata never exposes unrelated reports', () => {
  assert.deepEqual(filterMonitoring([product('a')], { ...DEFAULT_FILTERS, disease: 'D999' }, AS_OF, site, []), []);
  assert.deepEqual(filterMonitoring([product('a')], { ...DEFAULT_FILTERS, country: 'UNKNOWN' }, AS_OF, site, []), []);
});

test('monitoring window includes its boundary and orders newest reports first', () => {
  const rows = [product('boundary', { published_date: '2026-11-08' }), product('old', { published_date: '2026-11-07' }), product('latest')];
  assert.deepEqual(filterMonitoring(rows, { ...DEFAULT_FILTERS, window: '7' }, AS_OF, site, points).map(row => row.notice_id), ['latest', 'boundary']);
});
