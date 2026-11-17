import assert from 'node:assert/strict';
import test from 'node:test';
import { IPSN_RESOURCE_URL, pathogenResourceLabels, pathogenResourceProfile } from './pathogen-resources.ts';

test('uses the pathogen taxonomy rather than a clinical disease name as the sequence search', () => {
  const covid = pathogenResourceProfile('d004');
  assert.ok(covid);
  assert.equal(covid.pathogen, 'SARS-CoV-2');
  assert.equal(new URL(covid.sequenceUrl).searchParams.get('term'), 'txid2697049[Organism:exp]');
  const h5n1 = pathogenResourceProfile('D014');
  assert.ok(h5n1);
  assert.equal(new URL(h5n1.sequenceUrl).searchParams.get('term'), 'txid102793[Organism:exp]');
  assert.notEqual(h5n1.taxonomyId, 11320, 'H5N1 must not widen to all influenza A');
});

test('does not guess mappings for syndromes, broad influenza or unknown disease identifiers', () => {
  for (const disease of ['D038', 'D127', 'COVID-19', 'SARS-CoV-2', 'D999', '', '__proto__', 'constructor']) {
    assert.equal(pathogenResourceProfile(disease), null, disease);
  }
});

test('public searches and account-based resources remain distinct and on official hosts', () => {
  for (const disease of ['D001', 'D004', 'D014', 'D017', 'D021', 'D028']) {
    const profile = pathogenResourceProfile(disease);
    assert.ok(profile);
    assert.equal(new URL(profile.sequenceUrl).hostname, 'www.ncbi.nlm.nih.gov');
    assert.equal(new URL(profile.taxonomyUrl).searchParams.get('id'), String(profile.taxonomyId));
    assert.equal(new URL(profile.sequenceUrl).protocol, 'https:');
    assert.equal(Boolean(profile.gisaidUrl), ['D004', 'D014'].includes(disease));
    if (profile.gisaidUrl) assert.equal(profile.gisaidUrl, 'https://gisaid.org/');
  }
  assert.equal(new URL(IPSN_RESOURCE_URL).hostname, 'www.who.int');
});

test('all three locales explain sequence-count limits and GISAID account access', () => {
  assert.match(pathogenResourceLabels.en.intro, /sequence counts do not measure case numbers/);
  assert.match(pathogenResourceLabels.zh.intro, /序列数量不代表病例数/);
  assert.match(pathogenResourceLabels.fr.intro, /nombre de séquences ne mesure ni les cas/);
  for (const locale of ['en', 'zh', 'fr'] as const) {
    assert.ok(pathogenResourceLabels[locale].account);
    assert.ok(pathogenResourceLabels[locale].gisaidDescription.includes('GISAID'));
    assert.ok(pathogenResourceLabels[locale].ipsnDescription);
  }
});
