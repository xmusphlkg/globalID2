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
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
