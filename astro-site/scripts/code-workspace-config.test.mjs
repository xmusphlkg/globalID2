import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';

test('template workspace keeps Astro and Vite caches out of linked dependencies', async () => {
  const names = ['GIDS_RESEARCH_BUILD_DIR', 'GIDS_REPORTS_BUILD_DIR', 'GIDS_COUNTRY_BUILD_DIR', 'GIDS_CODE_BUILD_DIR', 'GIDS_BUILD_PUBLIC_DIR', 'GIDS_SITE_BUILD_OUTPUT_DIR'];
  const before = Object.fromEntries(names.map(name => [name, process.env[name]]));
  const fixture = mkdtempSync(resolve(tmpdir(), 'gids-physical-config-'));
  const actual = resolve(fixture, 'actual'), linked = resolve(fixture, 'linked');
  mkdirSync(resolve(actual, 'site'), { recursive: true });
  mkdirSync(resolve(actual, 'public'));
  symlinkSync(actual, linked, 'dir');
  try {
    for (const name of names) delete process.env[name];
    process.env.GIDS_CODE_BUILD_DIR = resolve(linked, 'site');
    process.env.GIDS_BUILD_PUBLIC_DIR = resolve(linked, 'public');
    process.env.GIDS_SITE_BUILD_OUTPUT_DIR = resolve(linked, 'site/.dist-build-test');
    const { default: config } = await import('../astro.config.mjs?private-code-test');
    assert.equal(config.root, resolve(actual, 'site'));
    assert.equal(config.srcDir, resolve(actual, 'site/src'));
    assert.equal(config.cacheDir, resolve(actual, 'site/.astro'));
    assert.equal(config.vite.cacheDir, resolve(actual, 'site/.vite'));
    assert.equal(config.publicDir, resolve(actual, 'public'));
    assert.equal(config.outDir, resolve(actual, 'site/.dist-build-test'));
  } finally {
    for (const name of names) {
      if (before[name] === undefined) delete process.env[name];
      else process.env[name] = before[name];
    }
    rmSync(fixture, { recursive: true, force: true });
  }
});
