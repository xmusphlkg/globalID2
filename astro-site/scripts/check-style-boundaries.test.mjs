import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import test from 'node:test';
import { auditStyleBoundaries, GLOBAL_STYLES_LINE_BUDGET } from './check-style-boundaries.mjs';

function withFixture(run) {
  const root = mkdtempSync(join(tmpdir(), 'gids-style-boundaries-'));
  const write = (rel, body) => {
    const full = join(root, rel);
    mkdirSync(join(full, '..'), { recursive: true });
    writeFileSync(full, body);
  };
  try {
    run({ root, write });
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

test('repository stylesheets pass all modular style boundaries within budget', () => {
  const projectRoot = resolve(import.meta.dirname, '..');
  const result = auditStyleBoundaries({ projectRoot });
  assert.equal(result.passed, true, result.failures.join('\n'));
  assert.ok(result.styleFiles >= 7, `expected modularized style files, got ${result.styleFiles}`);
  assert.ok(result.styleLines <= GLOBAL_STYLES_LINE_BUDGET);
});

test('enforces combined line budget, forbidden tokens, broken imports, and !important across src/styles/*.css', () => withFixture(({ root, write }) => {
  write('src/styles/global.css', "@import './tokens.css';\n@import './editorial.css';\n");
  write('src/styles/tokens.css', ':root { --bg: 12 25 38; }\n');
  write('src/styles/editorial.css', '@media (prefers-reduced-motion: reduce) { * { animation-duration: .01ms !important; } }\n');
  write('src/components/Card.astro', '<div class="card">ok</div>\n');

  const ok = auditStyleBoundaries({ projectRoot: root, lineBudget: 20 });
  assert.equal(ok.passed, true, ok.failures.join('\n'));
  assert.equal(ok.styleFiles, 3);

  const overBudget = auditStyleBoundaries({ projectRoot: root, lineBudget: 3 });
  assert.equal(overBudget.passed, false);
  assert.match(overBudget.failures.join('\n'), /combined lines; budget is 3/);

  write('src/styles/tokens.css', ':root { --compat-bg: 0 0 0; }\n.x { color: var(--slate-500); }\n');
  const badPartial = auditStyleBoundaries({ projectRoot: root, lineBudget: 50 });
  assert.equal(badPartial.passed, false);
  assert.match(badPartial.failures.join('\n'), /tokens\.css reintroduced compatibility color variables/);
  assert.match(badPartial.failures.join('\n'), /tokens\.css reintroduced a Tailwind slate presentation color/);

  write('src/styles/tokens.css', ':root { --bg: 12 25 38; }\n.bad { color: red !important; }\n');
  const badImportant = auditStyleBoundaries({ projectRoot: root, lineBudget: 50 });
  assert.equal(badImportant.passed, false);
  assert.match(badImportant.failures.join('\n'), /unapproved !important/);

  write('src/styles/tokens.css', ':root { --bg: 12 25 38; }\n');
  write('src/styles/global.css', "@import './tokens.css';\n@import './missing.css';\n");
  const missingImport = auditStyleBoundaries({ projectRoot: root, lineBudget: 50 });
  assert.equal(missingImport.passed, false);
  assert.match(missingImport.failures.join('\n'), /imports missing partial \.\/missing\.css/);
}));
