import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { buildSituationRedirectRules, renderRedirects } from './build-redirects.mjs';

test('builds real 301 rules for every legacy weekly and monthly situation URL', () => {
  const fixtureRoot = mkdtempSync(join(tmpdir(), 'gids-redirects-'));
  try {
    mkdirSync(join(fixtureRoot, 'weekly'));
    mkdirSync(join(fixtureRoot, 'monthly'));
    writeFileSync(join(fixtureRoot, 'weekly', '2026-W34.json'), '{}');
    writeFileSync(join(fixtureRoot, 'monthly', '2026-08.json'), '{}');
    const rules = buildSituationRedirectRules(fixtureRoot);
    assert.ok(rules.some(rule => /^\/situation\/\d{4}-W\d{2}\/\s+\/situation\/weekly\//.test(rule)));
    assert.ok(rules.some(rule => /^\/situation\/\d{4}-\d{2}\/\s+\/situation\/monthly\//.test(rule)));
    assert.ok(rules.every(rule => rule.endsWith('  301')));
    assert.equal(rules.length, new Set(rules).size);
  } finally {
    rmSync(fixtureRoot, { recursive: true, force: true });
  }
});

test('appends generated redirects without changing hand-authored rules', () => {
  const output = renderRedirects('/old  /new  301\n', [
    '/situation/2026-W34/  /situation/weekly/2026-W34/  301',
  ]);
  assert.match(output, /^\/old  \/new  301/m);
  assert.match(output, /# Generated legacy Situation Room redirects/);
  assert.match(output, /\/situation\/2026-W34\/  \/situation\/weekly\/2026-W34\/  301/);
});
