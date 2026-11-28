import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { test } from 'node:test';
import { EXCLUDED_UNIT_TESTS, discoverUnitTests } from './run-all-unit-tests.mjs';

const siteRoot = resolve(import.meta.dirname, '..');

test('discoverUnitTests finds all deterministic tests under scripts/ and src/ in sorted order', () => {
  const files = discoverUnitTests({ root: siteRoot });
  assert.ok(files.length >= 97, `expected at least 97 unit tests, found ${files.length}`);
  assert.deepEqual(files, [...files].sort());
  assert.ok(files.includes('scripts/build-pipeline.test.mjs'));
  assert.ok(files.includes('scripts/run-all-unit-tests.test.mjs'));
  assert.ok(files.includes('src/lib/seo.test.ts'));
  assert.ok(files.includes('src/utils/internationalSnapshot.test.ts'));
  assert.ok(files.every((file) => !file.startsWith('e2e/')));
});

test('discoverUnitTests covers every explicit unit test listed in package.json scripts', () => {
  const pkg = JSON.parse(readFileSync(resolve(siteRoot, 'package.json'), 'utf8'));
  const discovered = new Set(discoverUnitTests({ root: siteRoot }));
  const referenced = Object.entries(pkg.scripts)
    .filter(([name]) => name.startsWith('test:') && name !== 'test:e2e' && name !== 'test:all')
    .flatMap(([, command]) => command.match(/(?:scripts|src)\/[^\s*]+\.test\.(?:ts|mts|js|mjs)/g) ?? []);

  assert.ok(referenced.length > 0);
  for (const file of referenced) {
    assert.ok(
      discovered.has(file) || EXCLUDED_UNIT_TESTS.has(file),
      `package.json test file missing from discoverUnitTests: ${file}`,
    );
  }
});

test('discoverUnitTests respects exclusions and ignores non-test files and build directories', () => {
  const root = mkdtempSync(join(tmpdir(), 'gids-discover-tests-'));
  try {
    mkdirSync(join(root, 'scripts', 'nested'), { recursive: true });
    mkdirSync(join(root, 'src', 'lib'), { recursive: true });
    mkdirSync(join(root, 'src', 'node_modules', 'pkg'), { recursive: true });
    mkdirSync(join(root, 'e2e'), { recursive: true });

    writeFileSync(join(root, 'scripts', 'alpha.test.mjs'), '');
    writeFileSync(join(root, 'scripts', 'nested', 'beta.test.ts'), '');
    writeFileSync(join(root, 'scripts', 'helper.mjs'), '');
    writeFileSync(join(root, 'src', 'lib', 'gamma.test.ts'), '');
    writeFileSync(join(root, 'src', 'lib', 'skip.test.ts'), '');
    writeFileSync(join(root, 'src', 'node_modules', 'pkg', 'ignored.test.ts'), '');
    writeFileSync(join(root, 'e2e', 'browser.test.ts'), '');

    const found = discoverUnitTests({
      root,
      exclude: new Set(['src/lib/skip.test.ts']),
    });
    assert.deepEqual(found, [
      'scripts/alpha.test.mjs',
      'scripts/nested/beta.test.ts',
      'src/lib/gamma.test.ts',
    ]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
