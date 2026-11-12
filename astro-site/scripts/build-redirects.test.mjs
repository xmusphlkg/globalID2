import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { buildSituationRedirectRules, renderRedirects, withoutPapersHostRedirects } from './build-redirects.mjs';

test('builds 301 rules for every published weekly digest and its month', () => {
  const fixtureRoot = mkdtempSync(join(tmpdir(), 'gids-redirects-'));
  try {
    mkdirSync(join(fixtureRoot, 'digests'));
    writeFileSync(join(fixtureRoot, 'digests', 'index.json'), JSON.stringify({ periods: [{ period_key: '2026-W34' }, { period_key: 'bad' }] }));
    const rules = buildSituationRedirectRules(fixtureRoot);
    assert.ok(rules.includes('/situation/2026-W34/  /situation/weekly/2026-W34/  301'));
    assert.ok(rules.includes('/situation/2026-08/  /situation/monthly/2026-08/  301'));
    assert.ok(rules.every(rule => rule.endsWith('  301')));
    assert.equal(rules.length, new Set(rules).size);
    assert.equal(rules.length, 4);
  } finally {
    rmSync(fixtureRoot, { recursive: true, force: true });
  }
});

test('no digest index means no generated redirects', () => {
  const fixtureRoot = mkdtempSync(join(tmpdir(), 'gids-redirects-'));
  try {
    assert.deepEqual(buildSituationRedirectRules(fixtureRoot), []);
  } finally {
    rmSync(fixtureRoot, { recursive: true, force: true });
  }
});

test('main-origin research drops papers-host article redirects and keeps the rest', () => {
  const base = [
    '# Research article pages, cards, and research site-data live on the papers host.',
    '/research/articles/*  https://papers.globalinfectiousdisease.com/research/articles/:splat  301',
    '/zh/research/articles/*  https://papers.globalinfectiousdisease.com/zh/research/articles/:splat  301',
    '/fr/research/articles/*  https://papers.globalinfectiousdisease.com/fr/research/articles/:splat  301',
    '/research/cards/*  https://papers.globalinfectiousdisease.com/research/cards/:splat  301',
    '/site-data/research/*  https://papers.globalinfectiousdisease.com/site-data/research/:splat  301',
    '/privacy/  /terms/  301',
  ].join('\n');
  const output = renderRedirects(base, ['/situation/2026-W34/  /situation/weekly/2026-W34/  301'], { researchOnMain: true });
  assert.equal(output.includes('papers.globalinfectiousdisease.com'), false);
  assert.match(output, /\/privacy\/  \/terms\/  301/);
  assert.match(output, /\/situation\/2026-W34\//);
  assert.match(withoutPapersHostRedirects(base), /\/privacy\//);
});

test('appends generated redirects without changing hand-authored rules', () => {
  const output = renderRedirects('/old  /new  301\n', [
    '/situation/2026-W34/  /situation/weekly/2026-W34/  301',
  ]);
  assert.match(output, /^\/old  \/new  301/m);
  assert.match(output, /# Generated legacy Situation Room redirects/);
  assert.match(output, /\/situation\/2026-W34\/  \/situation\/weekly\/2026-W34\/  301/);
});
