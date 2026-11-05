import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { pageSourceSnapshot } from './page-build-cache.mjs';

const writeJson = (root, relativePath, value) => {
  const path = join(root, relativePath);
  mkdirSync(join(path, '..'), { recursive: true });
  writeFileSync(path, JSON.stringify(value));
};

test('tracks report archive, overview, and disease page bundles independently', () => {
  const root = mkdtempSync(join(tmpdir(), 'gids-report-page-cache-'));
  try {
    const dataRoot = join(root, 'data');
    writeJson(dataRoot, 'reports/index.json', [
      { id: 50, country_code: 'JP', schema_version: 'report_v4.0', title: 'Weekly report' },
    ]);
    writeJson(dataRoot, 'reports/50.json', {
      id: 50,
      country_code: 'JP',
      report_document_v4: {
        disease_directory: [{ slug: 'influenza', disease_id: 'J10', signal_count: 1 }],
      },
    });
    writeJson(dataRoot, 'meta.json', { countries: [] });
    writeJson(dataRoot, 'diseases/index.json', []);
    writeJson(dataRoot, 'downloads.json', { countries: [], diseases: [] });

    const first = pageSourceSnapshot({ dataRoot, shared: 'stable-template' });
    const templateChange = pageSourceSnapshot({ dataRoot, shared: 'stable-template', reportDiseaseTemplateHash: 'new-title-template' });
    assert.equal(templateChange.shared, first.shared);
    assert.deepEqual(Object.keys(first.articles).filter(key => first.articles[key].fingerprint !== templateChange.articles[key].fingerprint), ['report-disease:jp:50:influenza']);
    assert.deepEqual(first.articles['report-archive:jp'].paths, [
      'countries/jp/reports/index.html',
      'zh/countries/jp/reports/index.html',
      'fr/countries/jp/reports/index.html',
    ]);
    assert.deepEqual(first.articles['report:jp:50'].paths, [
      'countries/jp/reports/50/index.html',
      'zh/countries/jp/reports/50/index.html',
      'fr/countries/jp/reports/50/index.html',
    ]);
    assert.deepEqual(first.articles['report-disease:jp:50:influenza'].paths, [
      'countries/jp/reports/50/influenza/index.html',
      'zh/countries/jp/reports/50/influenza/index.html',
      'fr/countries/jp/reports/50/influenza/index.html',
    ]);

    writeJson(dataRoot, 'reports/50.json', {
      id: 50,
      country_code: 'JP',
      report_document_v4: {
        disease_directory: [{ slug: 'influenza', disease_id: 'J10', signal_count: 2 }],
      },
    });
    const changed = pageSourceSnapshot({ dataRoot, shared: 'stable-template' });
    assert.equal(changed.articles['report-archive:jp'].fingerprint,
      first.articles['report-archive:jp'].fingerprint);
    assert.notEqual(changed.articles['report:jp:50'].fingerprint,
      first.articles['report:jp:50'].fingerprint);
    assert.notEqual(changed.articles['report-disease:jp:50:influenza'].fingerprint,
      first.articles['report-disease:jp:50:influenza'].fingerprint);

    writeJson(dataRoot, 'reports/index.json', [
      { id: 50, country_code: 'JP', schema_version: 'report_v4.0', title: 'Weekly report', created_at: '2026-10-01' },
      { id: 51, country_code: 'JP', schema_version: 'report_v4.0', title: 'Next report', created_at: '2026-10-08' },
    ]);
    writeJson(dataRoot, 'reports/51.json', {
      id: 51, country_code: 'JP', report_document_v4: { disease_directory: [] },
    });
    const withNeighbor = pageSourceSnapshot({ dataRoot, shared: 'stable-template' });
    writeJson(dataRoot, 'reports/index.json', [
      { id: 50, country_code: 'JP', schema_version: 'report_v4.0', title: 'Weekly report', created_at: '2026-10-01' },
    ]);
    const withoutNeighbor = pageSourceSnapshot({ dataRoot, shared: 'stable-template' });
    assert.notEqual(withNeighbor.articles['report:jp:50'].fingerprint,
      withoutNeighbor.articles['report:jp:50'].fingerprint);
    assert.equal(withNeighbor.articles['report-disease:jp:50:influenza'].fingerprint,
      withoutNeighbor.articles['report-disease:jp:50:influenza'].fingerprint);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('country changes invalidate only consumed data, alerts, reports and ranking memberships', () => {
  const root = mkdtempSync(join(tmpdir(), 'gids-country-page-cache-'));
  try {
    const country = (code, score) => ({ country_code: code, source_info: { primary_url: 'https://example.invalid' },
      total_cases: score, disease_series: { flu: { slug: 'flu', total_cases: score, available_series_count: 1,
        period_granularity: 'annual', dates: ['2020', '2021', '2022', '2023', '2024'] } } });
    const countries = Array.from({ length: 51 }, (_, index) => ({ code: `C${index}`, data_available: true }));
    writeJson(root, 'meta.json', { countries });
    for (const [index, entry] of countries.entries()) writeJson(root, `countries/${entry.code.toLowerCase()}.json`, country(entry.code, 1000 - index));
    writeJson(root, 'reports/index.json', []);
    const snapshot = () => pageSourceSnapshot({ dataRoot: root, shared: 'templates' }).articles;
    const first = snapshot();
    // The winner's score changes without changing membership; other countries keep their pages.
    writeJson(root, 'countries/c0.json', country('C0', 2000));
    const score = snapshot();
    assert.notEqual(score['country:c0'].fingerprint, first['country:c0'].fingerprint);
    assert.equal(score['country:c1'].fingerprint, first['country:c1'].fingerprint);
    // The outsider enters the top 50, displacing only C49's link.
    writeJson(root, 'countries/c50.json', country('C50', 3000));
    const ranked = snapshot();
    assert.notEqual(ranked['country:c49'].fingerprint, score['country:c49'].fingerprint);
    assert.equal(ranked['country:c1'].fingerprint, score['country:c1'].fingerprint);
    writeJson(root, 'situation/international/by-country/C0.json', { schema_version: 'gids.situation_alerts.v1', alerts: [{ alert_id: 'one' }] });
    writeJson(root, 'reports/index.json', [{ id: 1, country_code: 'C0', title: 'A report' }]);
    const ownFeeds = snapshot();
    assert.notEqual(ownFeeds['country:c0'].fingerprint, ranked['country:c0'].fingerprint);
    assert.equal(ownFeeds['country:c1'].fingerprint, ranked['country:c1'].fingerprint);
    // Parent page uses its children's metadata cards; unrelated countries do not.
    countries[2] = { ...countries[2], parent_code: 'C1', record_count: 23 };
    writeJson(root, 'meta.json', { countries });
    const child = snapshot();
    assert.notEqual(child['country:c1'].fingerprint, ownFeeds['country:c1'].fingerprint);
    assert.equal(child['country:c3'].fingerprint, ownFeeds['country:c3'].fingerprint);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
