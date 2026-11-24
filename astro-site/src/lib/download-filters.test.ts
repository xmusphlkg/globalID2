import assert from 'node:assert/strict';
import test from 'node:test';
import { downloadSelection, downloadWindowLabel } from './download-filters.ts';

const parts = [
  { country_code: 'JP', disease_id: 'D007', id: '2020-2025', label: 'D007 · 2020–2025' },
  { country_code: 'JP', disease_id: 'D038', id: '2020-2025', label: 'D038 · 2020–2025' },
  { country_code: 'JP', disease_id: 'D038', id: '2026-2029', label: 'D038 · 2026–now' },
  { country_code: 'BG', disease_id: 'D038', id: '2020-2025', label: 'BG · 2020–2025' },
];

test('country filters retain all diseases by default and select disease and window independently', () => {
  const country = parts.filter(part => part.country_code === 'JP');
  const all = downloadSelection(country, 'disease_id');
  assert.deepEqual(all.scopes, ['D007', 'D038']);
  assert.equal(all.parts.length, 3);
  assert.deepEqual(all.windows.map(window => window.id), ['2020-2025', '2026-2029']);
  assert.deepEqual(downloadSelection(country, 'disease_id', 'all', '2020-2025').parts, country.slice(0, 2));
  assert.deepEqual(downloadSelection(country, 'disease_id', 'D038', '2020-2025').parts, [parts[1]]);
});

test('disease filters expose every country and resolve the same shared cell as the country view', () => {
  const disease = parts.filter(part => part.disease_id === 'D038');
  assert.deepEqual(downloadSelection(disease, 'country_code').scopes, ['JP', 'BG']);
  assert.deepEqual(downloadSelection(disease, 'country_code', 'BG').windows, [{ id: '2020-2025', label: '2020–2025' }]);
  assert.deepEqual(downloadSelection(disease, 'country_code', 'JP', '2020-2025').parts,
    downloadSelection(parts.filter(part => part.country_code === 'JP'), 'disease_id', 'D038', '2020-2025').parts);
});

test('legacy catalogues retain time filtering without inventing a scope', () => {
  const legacy = [{ id: '2025', label: '2025' }, { id: '2026', label: '2026' }];
  assert.deepEqual(downloadSelection(legacy, 'disease_id').scopes, []);
  assert.deepEqual(downloadSelection(legacy, 'disease_id', 'all', '2025').parts, [legacy[0]]);
});

import { readDownloadFilters, downloadCenterSelection } from './download-filters.ts';
test('center deep links normalize identifiers and reject unsafe or unsupported selectors', () => {
  assert.deepEqual(readDownloadFilters('?country=jp&disease=d038&window=2026-2029&format=csv'),
    { country: 'JP', disease: 'D038', window: '2026-2029', format: 'csv' });
  assert.deepEqual(readDownloadFilters('?country=../JP&window=%3Cscript%3E&format=exe'),
    { country: '', disease: '', window: '', format: '' });
});
test('center applies country, disease and window together without losing shared-window files', () => {
  const filters = readDownloadFilters('?disease=D038&window=2020-2025');
  assert.deepEqual(downloadCenterSelection(parts, filters).parts, [parts[1], parts[3]]);
  assert.deepEqual(downloadCenterSelection(parts, { ...filters, country: 'JP' }).parts, [parts[1]]);
});

test('historical archive labels never imply coverage through today', () => {
  const options = { currentYear: 2030 };
  assert.equal(downloadWindowLabel({ id: '2020-2025', label: 'D038 · 2020–now', is_current: true }, options), '2020–2025');
  assert.equal(downloadWindowLabel({ id: '2026-2029', label: 'D038 · 2026–now', is_current: true }, options), '2026–2029');
});

test('current calendar windows use localized now labels without changing their IDs', () => {
  for (const currentYear of [2026, 2029]) {
    const part = { id: '2026-2029', label: 'D038 · 2026–2029', is_current: false };
    assert.equal(downloadWindowLabel(part, { currentYear }), '2026–now');
    assert.equal(downloadWindowLabel(part, { currentYear, language: 'zh' }), '2026–至今');
    assert.equal(downloadWindowLabel(part, { currentYear, language: 'fr' }), '2026–aujourd’hui');
    assert.equal(part.id, '2026-2029');
  }
  assert.equal(downloadWindowLabel({ id: '2030-2034' }, { currentYear: 2029 }), '2030–2034');
});

test('filters and file labels roll over automatically in 2030 and every five years after', () => {
  const archives = ['2026-2029', '2030-2034', '2035-2039'].map(id => ({ id }));
  for (const currentYear of [2030, 2034, 2035]) {
    const options = { currentYear, language: 'zh' as const };
    const filters = readDownloadFilters('?window=2030-2034');
    const selection = downloadCenterSelection(archives, filters, options);
    assert.deepEqual(selection.windows, [
      { id: '2026-2029', label: '2026–2029' },
      { id: '2030-2034', label: currentYear <= 2034 ? '2030–至今' : '2030–2034' },
      { id: '2035-2039', label: currentYear >= 2035 ? '2035–至今' : '2035–2039' },
    ]);
    assert.deepEqual(selection.parts, [archives[1]]);
    assert.equal(downloadWindowLabel(selection.parts[0], options), selection.windows[1].label);
  }
});
