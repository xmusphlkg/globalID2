import assert from 'node:assert/strict';
import test from 'node:test';
import {
  buildPublishableReports,
  isReportV4,
  normalizeReportRouteSegment,
  reportArchiveCountries,
  reportDiseaseSlugs,
  reportsForCountry,
} from './report-routes.ts';

test('recognizes every report_v4 marker used by report detail routes', () => {
  assert.equal(isReportV4({ metadata: { report_layout: 'report_v4' } }), true);
  assert.equal(isReportV4({ metadata: { report_document_v4: {} } }), true);
  assert.equal(isReportV4({ report_document_v4: {} }), true);
  assert.equal(isReportV4({ schema_version: 'report_v4.0' }), true);
  assert.equal(isReportV4({ metadata: { report_layout: 'report_v3' } }), false);
  assert.equal(isReportV4(null), false);
});

test('normalizes valid route segments and rejects path-breaking values', () => {
  assert.equal(normalizeReportRouteSegment(' JP ', true), 'jp');
  assert.equal(normalizeReportRouteSegment(50), '50');

  for (const invalid of ['', ' ', '.', '..', 'a/b', 'a\\b', 'a?b', 'a#b', null]) {
    assert.equal(normalizeReportRouteSegment(invalid), null);
  }
});

test('takes unique, valid disease slugs only from the v4 detail document', () => {
  const detail = {
    report_document_v4: {
      disease_directory: [
        { slug: ' influenza ' },
        { slug: 'measles' },
        { slug: 'influenza' },
        { slug: '' },
        { slug: '.' },
        { slug: '..' },
        { slug: 'bad/slug' },
        { slug: 'bad\\slug' },
        { slug: 'bad?slug' },
        { slug: 'bad#slug' },
        {},
      ],
    },
  };

  assert.deepEqual(reportDiseaseSlugs(detail), ['influenza', 'measles']);
  assert.deepEqual(reportDiseaseSlugs({ disease_directory: 'not-an-array' }), []);
});

test('builds the single publishable set used by archives and report routes', () => {
  const summaries = [
    { id: 10, country_code: ' JP ', metadata: { report_layout: 'report_v4' } },
    { id: 10, country_code: 'jp', schema_version: 'report_v4.0' },
    { id: 11, country_code: 'JP', metadata: { report_layout: 'report_v3' } },
    { id: 12, country_code: 'JP', schema_version: 'report_v4.0' },
    { id: 13, country_code: 'JP', schema_version: 'report_v4.0' },
    { id: 14, country_code: '../US', schema_version: 'report_v4.0' },
    { id: 'bad/id', country_code: 'US', schema_version: 'report_v4.0' },
    { id: 16, country_code: 'US', schema_version: 'report_v4.0' },
    { id: 17, country_code: 'US', schema_version: 'report_v4.0' },
    { id: 18, country_code: 'US', schema_version: 'report_v4.0' },
  ];
  const details = new Map<string, unknown>([
    ['10', {
      id: '10',
      country_code: 'jp',
      report_document_v4: {
        disease_directory: [
          { slug: 'influenza' },
          { slug: 'influenza' },
          { slug: 'bad/slug' },
        ],
      },
    }],
    ['11', { id: 11, country_code: 'JP', schema_version: 'report_v4.0' }],
    ['13', { id: 13, country_code: 'JP', metadata: { report_layout: 'report_v3' } }],
    ['16', { id: 99, country_code: 'US', schema_version: 'report_v4.0' }],
    ['17', { id: 17, country_code: 'CN', schema_version: 'report_v4.0' }],
    ['18', { id: 18, country_code: 'us', schema_version: 'report_v4.0' }],
  ]);
  const loadedIds: string[] = [];

  const reports = buildPublishableReports(summaries, (id) => {
    loadedIds.push(id);
    return details.get(id) ?? null;
  });

  assert.deepEqual(reports.map(({ country, diseaseSlugs, id }) => ({
    country,
    diseaseSlugs,
    id,
  })), [
    { country: 'jp', diseaseSlugs: ['influenza'], id: '10' },
    { country: 'us', diseaseSlugs: [], id: '18' },
  ]);
  assert.deepEqual(loadedIds, ['10', '12', '13', '16', '17', '18']);
  assert.deepEqual(reportArchiveCountries(reports), ['jp', 'us']);
  assert.deepEqual(reportsForCountry(reports, 'JP').map((report) => report.id), [10]);
  assert.deepEqual(reportsForCountry(reports, undefined), []);
  assert.deepEqual(reportArchiveCountries(summaries), []);
});
