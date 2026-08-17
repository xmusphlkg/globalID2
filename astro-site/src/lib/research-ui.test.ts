import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const sourceRoot = fileURLToPath(new URL('../', import.meta.url));
const readSource = (relativePath: string) => readFileSync(`${sourceRoot}/${relativePath}`, 'utf-8');
const researchData = JSON.parse(readSource('data/research/index.json')) as Record<string, any>;
const hotspotData = JSON.parse(readSource('data/research/hotspots.json')) as Record<string, any>;

test('current Research Radar data can populate every homepage intelligence section', () => {
  assert.ok(Array.isArray(researchData.reviews_and_guidelines));
  assert.ok(researchData.reviews_and_guidelines.length > 0);
  assert.ok(Array.isArray(researchData.emerging_topics));
  assert.ok(researchData.emerging_topics.length > 0);
  assert.equal(researchData.surveillance_evidence?.available, true);
  assert.equal(researchData.surveillance_evidence?.visibility, 'public');
  assert.ok(researchData.surveillance_evidence.signals.some((signal: any) => signal.visibility === 'public'));

  for (const metric of [
    'papers_last_7_days',
    'diseases_last_7_days',
    'countries_last_7_days',
    'reviews_and_guidelines_last_7_days',
  ]) assert.equal(typeof researchData.metrics?.[metric], 'number', metric);

  assert.match(hotspotData.interpretation_note?.en ?? '', /not disease risk/i);
  assert.match(hotspotData.interpretation_note?.zh ?? '', /不代表疾病风险/);
});

test('Research homepage renders collections, seven-day metrics, and the trend interpretation', () => {
  const homepage = readSource('pages/research/index.astro');
  const charts = readSource('components/research/ResearchHotspotCharts.tsx');

  for (const heading of ['Surveillance-linked Research', 'New Reviews & Guidelines', 'Emerging Topics']) {
    assert.match(homepage, new RegExp(heading.replace(/[&]/g, '\\&')));
  }
  assert.match(homepage, /metrics\.countries_last_7_days/);
  assert.match(homepage, /metrics\.reviews_and_guidelines_last_7_days/);
  assert.match(homepage, /surveillanceEvidence\.methodology/);
  assert.match(homepage, /Increased attention does not mean increased disease risk/);
  assert.match(charts, /hotspots\.interpretation_note\?\.en/);
  assert.match(charts, /not disease risk or incidence/);
});

test('static and dynamically rendered research cards expose the same discovery actions', () => {
  const component = readSource('components/research/ResearchArticleCard.astro');
  const homepage = readSource('pages/research/index.astro');
  const catalogue = readSource('lib/research-catalogue.ts');

  for (const marker of ['research-card-authors', 'research-badge-topic', 'research-card-action-oa', 'surveillanceUrl']) {
    assert.match(component, new RegExp(marker));
    assert.match(homepage, new RegExp(marker));
  }
  assert.match(catalogue, /authors: article\.authors/);
  assert.match(catalogue, /related_surveillance: article\.related_surveillance/);
});

test('article evidence pages disclose publication metadata and AI-assisted provenance', () => {
  const detail = readSource('pages/research/articles/[slug].astro');
  assert.match(detail, /data-lang-en="Publisher"/);
  assert.match(detail, /data-lang-en="Article type"/);
  assert.match(detail, /AI-assisted summary provenance/);
  assert.match(detail, /has not been editorially approved/);
  assert.match(detail, /summaryProvenance\.publication_gate/);
});

test('weekly briefs render a human reviewer only for an explicit reviewed status', () => {
  const weekly = readSource('pages/research/weekly/[week].astro');

  assert.match(weekly, /payload\.brief_status === 'editorially_reviewed'/);
  assert.match(weekly, /reviewer\.name/);
  assert.match(weekly, /reviewer\.role/);
  assert.match(weekly, /reviewer\.reviewed_at/);
  assert.match(weekly, /Editorially reviewed by/);
  assert.match(weekly, /编辑审核/);
  assert.match(weekly, /Automatically compiled · not editorially reviewed/);
  assert.match(weekly, /自动编译 · 未经编辑审核/);
  assert.match(weekly, /data-review-status="not-editorially-reviewed"/);
});
