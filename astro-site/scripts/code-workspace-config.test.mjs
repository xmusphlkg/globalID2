import test from 'node:test';
import assert from 'node:assert/strict';
import { resolve } from 'node:path';

test('template workspace keeps Astro and Vite caches out of linked dependencies', async () => {
  const names = ['GIDS_RESEARCH_BUILD_DIR', 'GIDS_REPORTS_BUILD_DIR', 'GIDS_COUNTRY_BUILD_DIR', 'GIDS_CODE_BUILD_DIR', 'GIDS_BUILD_PUBLIC_DIR'];
  const before = Object.fromEntries(names.map(name => [name, process.env[name]]));
  try {
    for (const name of names) delete process.env[name];
    process.env.GIDS_CODE_BUILD_DIR = '/tmp/gids-private-code-site';
    process.env.GIDS_BUILD_PUBLIC_DIR = '/tmp/gids-private-public-stage';
    const { default: config } = await import('../astro.config.mjs?private-code-test');
    assert.equal(config.root, resolve(process.env.GIDS_CODE_BUILD_DIR));
    assert.equal(config.cacheDir, resolve(process.env.GIDS_CODE_BUILD_DIR, '.astro'));
    assert.equal(config.vite.cacheDir, resolve(process.env.GIDS_CODE_BUILD_DIR, '.vite'));
    assert.equal(config.publicDir, resolve(process.env.GIDS_BUILD_PUBLIC_DIR));
    assert.equal(config.outDir, resolve(process.env.GIDS_CODE_BUILD_DIR, 'dist'));
  } finally {
    for (const name of names) {
      if (before[name] === undefined) delete process.env[name];
      else process.env[name] = before[name];
    }
  }
});
