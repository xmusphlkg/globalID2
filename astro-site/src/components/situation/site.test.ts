import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { buildSituationSite, seriesGlance, seriesKey } from './site.ts';

test('a glance uses the newest non-zero point and keeps the tail for the sparkline', () => {
  const glance = seriesGlance([1, 4, 0, 0], ['2020-01-01', '2021-01-01', '2022-01-01', '2023-01-01']);
  assert.equal(glance?.value, 4);
  assert.equal(glance?.date, '2021-01-01');
  assert.equal(glance?.changePct, 300);
  assert.deepEqual(glance?.spark, [1, 4, 0, 0]);
});

test('an all-zero series still reports the latest zero, and empty input is absent', () => {
  const zeros = seriesGlance([0, 0], ['2024-01-01', '2025-01-01']);
  assert.equal(zeros?.value, 0);
  assert.equal(zeros?.date, '2025-01-01');
  assert.equal(zeros?.changePct, undefined);
  assert.equal(seriesGlance([null, null], ['2024-01-01', '2025-01-01']), null);
  assert.equal(seriesGlance(null, null), null);
});

test('the site index prefixes catalogue links and sparks only the requested pairs', () => {
  const site = buildSituationSite({
    prefix: '/zh',
    curveKeys: ['jp:influenza'],
    sparkPairs: ['jp:d038'],
    diseases: [{
      id: 'd038',
      slug: 'influenza',
      name_en: 'Influenza',
      name_zh: '流感',
      researchCount: 12,
      researchUrl: '/research/diseases/influenza/',
    }],
    countries: [
      {
        code: 'jp',
        name_en: 'Japan',
        name_zh: '日本',
        diseaseCount: 40,
        latestDate: '2026-01-01',
        page: true,
        researchCount: 8,
        researchUrl: '/research/countries/jp/',
        series: {
          D038: { cases: [1, 2, 3], dates: ['2022-01-01', '2023-01-01', '2024-01-01'], slug: 'influenza' },
        },
      },
      {
        code: 'KE',
        name_en: 'Kenya',
        diseaseCount: 0,
        page: false,
        researchCount: 3,
        researchUrl: '/research/countries/ke/',
        series: {},
      },
    ],
  });

  assert.equal(seriesKey('jp', 'd038'), 'JP:D038');
  assert.equal(site.countries.JP?.href, '/zh/countries/jp/');
  assert.equal(site.countries.JP?.researchHref, '/zh/research/countries/jp/');
  assert.equal(site.countries.KE?.href, undefined);
  assert.equal(site.countries.KE?.researchHref, '/zh/research/countries/ke/');
  assert.equal(site.diseases.D038?.href, '/zh/diseases/influenza/');
  assert.equal(site.diseases.D038?.researchHref, '/zh/research/diseases/influenza/');
  assert.equal(site.series['JP:D038']?.curveHref, '/zh/countries/jp/diseases/influenza/');
  assert.deepEqual(site.series['JP:D038']?.spark, [1, 2, 3]);
  assert.equal(site.series['JP:D038']?.value, 3);
  assert.equal(site.series['JP:D038']?.changePct, 50);
});

test('a series that is not on the map omits the spark and an unpublished curve', () => {
  const site = buildSituationSite({
    curveKeys: [],
    sparkPairs: [],
    diseases: [],
    countries: [{
      code: 'es',
      name_en: 'Spain',
      diseaseCount: 52,
      page: true,
      series: { d128: { cases: [5, 0], dates: ['2023-01-01', '2024-01-01'], slug: 'West Nile Virus' } },
    }],
  });
  const glance = site.series['ES:D128'];
  assert.ok(glance);
  assert.equal(glance?.value, 5);
  assert.equal(glance?.spark, undefined);
  assert.equal(glance?.curveHref, undefined);
});
