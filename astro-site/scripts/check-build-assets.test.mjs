import assert from 'node:assert/strict';
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { test } from 'node:test';
import { auditBuildAssets } from './check-build-assets.mjs';

const site = resolve(import.meta.dirname, '..');
const temporary = () => {
  const cache = resolve(site, '../data/cache');
  mkdirSync(cache, { recursive: true });
  return mkdtempSync(resolve(cache, 'build-assets-test-'));
};

test('rejects omitted base CSS even when external theme and remaining assets exist', () => {
  const root = temporary();
  try {
    mkdirSync(resolve(root, '_astro'));
    writeFileSync(resolve(root, '_astro/page.js'), 'console.log("ready")');
    const html = '<link rel="stylesheet" href="https://theme.example/theme.css"><a class="skip-link" href="#main">Skip</a><script type="module" src="/_astro/page.js"></script>';
    writeFileSync(resolve(root, 'index.html'), html);
    assert.match(auditBuildAssets(root).errors.join('\n'), /missing bundled global layout CSS/);
    writeFileSync(resolve(root, '_astro/base.css'), '.skip-link{position:absolute}');
    writeFileSync(resolve(root, 'index.html'), '<link href="/_astro/base.css" rel="stylesheet">' + html);
    assert.equal(auditBuildAssets(root).passed, true);
    rmSync(resolve(root, '_astro/page.js'));
    assert.match(auditBuildAssets(root).errors.join('\n'), /missing local build asset/);
    writeFileSync(resolve(root, 'index.html'), '<style>.skip-link{position:absolute}</style><a class="skip-link">Skip</a>');
    assert.equal(auditBuildAssets(root).passed, true);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('real Astro build retains CSS and page scripts through a symlinked workspace ancestor', () => {
  const root = temporary();
  try {
    const actual = resolve(root, 'actual/site'), linked = resolve(root, 'linked/site');
    mkdirSync(resolve(actual, 'src/pages'), { recursive: true });
    mkdirSync(resolve(actual, 'public'));
    for (const name of ['astro.config.mjs', 'package.json', 'tsconfig.json']) cpSync(resolve(site, name), resolve(actual, name));
    symlinkSync(resolve(site, 'node_modules'), resolve(actual, 'node_modules'), 'dir');
    symlinkSync(resolve(root, 'actual'), resolve(root, 'linked'), 'dir');
    writeFileSync(resolve(actual, 'src/pages/index.astro'), '<html><head><title>Build asset fixture</title></head><body><a class="skip-link">Skip</a><main>Fixture</main><script>document.documentElement.dataset.assetFixture="ready";</script><style is:global>.skip-link { position: absolute; } body { margin: 0; }</style></body></html>');
    const env = { ...process.env, ASTRO_TELEMETRY_DISABLED: '1', XDG_CONFIG_HOME: resolve(root, '.config'), GIDS_RESEARCH_BUILD_DIR: '', GIDS_REPORTS_BUILD_DIR: '', GIDS_COUNTRY_BUILD_DIR: '', GIDS_CODE_BUILD_DIR: linked, GIDS_ASTRO_RENDER_WORKERS: '2', GIDS_BUILD_PUBLIC_DIR: '' };
    const result = spawnSync(process.execPath, [resolve(site, 'node_modules/astro/bin/astro.mjs'), 'build'], { cwd: actual, env, encoding: 'utf8', timeout: 60000 });
    assert.equal(result.status, 0, result.stdout + result.stderr);
    const html = readFileSync(resolve(actual, 'dist/index.html'), 'utf8');
    assert.match(html, /<script type="module">[^<]*assetFixture/);
    assert.deepEqual(auditBuildAssets(resolve(actual, 'dist')), { passed: true, pages: 1, errors: [] });
  } finally { rmSync(root, { recursive: true, force: true }); }
});
