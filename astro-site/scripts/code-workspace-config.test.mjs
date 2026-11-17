import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, realpathSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';

test('template workspace keeps Astro and Vite caches out of linked dependencies', async () => {
  const names = ['GIDS_RESEARCH_BUILD_DIR', 'GIDS_REPORTS_BUILD_DIR', 'GIDS_COUNTRY_BUILD_DIR', 'GIDS_CODE_BUILD_DIR', 'GIDS_BUILD_PUBLIC_DIR'];
  const before = Object.fromEntries(names.map(name => [name, process.env[name]]));
  const temporary = mkdtempSync(resolve(tmpdir(), 'gids-private-code-config-'));
  try {
    for (const name of names) delete process.env[name];
    mkdirSync(resolve(temporary, 'actual/site'), { recursive: true });
    symlinkSync(resolve(temporary, 'actual'), resolve(temporary, 'linked'), 'dir');
    process.env.GIDS_CODE_BUILD_DIR = resolve(temporary, 'linked/site');
    process.env.GIDS_BUILD_PUBLIC_DIR = resolve(temporary, 'public-stage');
    const physicalRoot = realpathSync(process.env.GIDS_CODE_BUILD_DIR);
    const { default: config } = await import('../astro.config.mjs?private-code-test');
    assert.equal(config.root, physicalRoot);
    assert.equal(config.cacheDir, resolve(physicalRoot, '.astro'));
    assert.equal(config.vite.cacheDir, resolve(physicalRoot, '.vite'));
    assert.equal(config.publicDir, resolve(process.env.GIDS_BUILD_PUBLIC_DIR));
    assert.equal(config.outDir, resolve(physicalRoot, 'dist'));
  } finally {
    for (const name of names) {
      if (before[name] === undefined) delete process.env[name];
      else process.env[name] = before[name];
    }
    rmSync(temporary, { recursive: true, force: true });
  }
});
