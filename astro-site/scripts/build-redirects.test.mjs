import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { buildSituationRedirectRules, renderRedirects } from './build-redirects.mjs';

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

test('appends generated redirects without changing hand-authored rules', () => {
  const output = renderRedirects('/old  /new  301\n', [
    '/situation/2026-W34/  /situation/weekly/2026-W34/  301',
  ]);
  assert.match(output, /^\/old  \/new  301/m);
  assert.match(output, /# Generated legacy Situation Room redirects/);
  assert.match(output, /\/situation\/2026-W34\/  \/situation\/weekly\/2026-W34\/  301/);
});
