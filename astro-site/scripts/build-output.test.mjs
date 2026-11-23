import assert from 'node:assert/strict';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { test } from 'node:test';
import { cleanAbandonedBuilds, promoteBuildOutput, validateStylesheets } from './build-output.mjs';

function fixture(run) {
  const root = mkdtempSync(join(tmpdir(), 'gids-build-output-'));
  const candidate = join(root, '.dist-build-test'), dist = join(root, 'dist');
  mkdirSync(candidate); mkdirSync(dist);
  writeFileSync(join(dist, 'index.html'), 'previous verified page');
  const write = (path, body) => {
    const full = join(candidate, path);
    mkdirSync(join(full, '..'), { recursive: true });
    writeFileSync(full, body);
  };
  try { run({ candidate, dist, write }); }
  finally { rmSync(root, { recursive: true, force: true }); }
}

const page = (links = '') => `<html><head>${links}</head><body><header class="site-header sticky">GIDS</header></body></html>`;
const cssLink = '<link href="/_astro/base.css" rel="stylesheet">';

test('cleans only abandoned build directories and preserves interrupted promotions', () => fixture(({ candidate, dist }) => {
  const site = resolve(dist, '..');
  const abandoned = join(site, '.dist-build-42-1000');
  const interrupted = join(site, '.dist-build-43-1001');
  const link = join(site, '.dist-build-44-1002');
  mkdirSync(abandoned);
  mkdirSync(interrupted);
  mkdirSync(interrupted + '.previous');
  symlinkSync(dist, link, 'dir');
  cleanAbandonedBuilds(site);
  cleanAbandonedBuilds(site);
  assert.equal(existsSync(abandoned), false);
  for (const path of [candidate, dist, interrupted, interrupted + '.previous', link]) assert.ok(existsSync(path));
}));

test('theme-only HTML and missing routes cannot replace the previous build', () => fixture(({ candidate, dist, write }) => {
  write('index.html', page('<link rel="stylesheet" href="https://theme.example/theme.css"><style>astro-island{display:contents}</style>'));
  assert.throws(() => { validateStylesheets(candidate); promoteBuildOutput(candidate, dist); }, /Missing base layout CSS/);
  assert.equal(readFileSync(join(dist, 'index.html'), 'utf8'), 'previous verified page');
  write('index.html', page(cssLink)); write('_astro/base.css', '.site-header{height:73px}');
  assert.throws(() => validateStylesheets(candidate, { requiredPaths: ['index.html', 'zh/index.html'] }), /omitted required route: zh/);
}));

test('checks styles of every nested page, including restored pages with missing files', () => fixture(({ candidate, write }) => {
  write('index.html', page(cssLink)); write('_astro/base.css', '.site-header{height:73px}');
  write('zh/countries/cn/index.html', page('<link rel="stylesheet" href="/_astro/gone.css">'));
  assert.throws(() => validateStylesheets(candidate), /Missing stylesheet.*gone.css.*zh\/countries\/cn/);
  write('zh/countries/cn/index.html', page(cssLink));
  assert.deepEqual(validateStylesheets(candidate), { pages: 2, styledPages: 2, stylesheets: 1 });
}));

test('rejects empty assets and allows fully inline styles and redirect-only documents', () => fixture(({ candidate, write }) => {
  write('index.html', page(cssLink)); write('_astro/base.css', '');
  assert.throws(() => validateStylesheets(candidate), /Empty stylesheet/);
  write('index.html', page('<style>.site-header{height:73px}</style>'));
  write('legacy/index.html', '<html><meta http-equiv="refresh" content="0;url=/"></html>');
  assert.deepEqual(validateStylesheets(candidate), { pages: 2, styledPages: 1, stylesheets: 0 });
}));

test('successful promotion replaces the full directory and removes the backup', () => fixture(({ candidate, dist, write }) => {
  write('index.html', page(cssLink)); write('_astro/base.css', '.site-header{height:73px}');
  validateStylesheets(candidate);
  promoteBuildOutput(candidate, dist);
  assert.equal(readFileSync(join(dist, 'index.html'), 'utf8'), page(cssLink));
  assert.equal(existsSync(candidate), false);
  assert.equal(existsSync(candidate + '.previous'), false);
}));

test('failure moving the original directory never deletes it', () => fixture(({ candidate, dist, write }) => {
  write('index.html', 'new page');
  assert.throws(() => promoteBuildOutput(candidate, dist, {
    rename() { throw new Error('original move denied'); },
  }), /original move denied/);
  assert.equal(readFileSync(join(dist, 'index.html'), 'utf8'), 'previous verified page');
  assert.equal(readFileSync(join(candidate, 'index.html'), 'utf8'), 'new page');
}));

test('failure moving candidate restores the complete original directory', () => fixture(({ candidate, dist, write }) => {
  write('index.html', 'new page');
  assert.throws(() => promoteBuildOutput(candidate, dist, {
    rename(from, to) { if (from === candidate) throw new Error('candidate move denied'); renameSync(from, to); },
  }), /candidate move denied/);
  assert.equal(readFileSync(join(dist, 'index.html'), 'utf8'), 'previous verified page');
  assert.equal(existsSync(candidate + '.previous'), false);
}));

test('backup cleanup failure keeps the new output instead of restoring a damaged backup', () => fixture(({ candidate, dist, write }) => {
  write('index.html', 'new page');
  const warnings = [];
  promoteBuildOutput(candidate, dist, {
    remove(path) { rmSync(join(path, 'index.html')); throw new Error('cleanup interrupted'); },
    warn(message) { warnings.push(message); },
  });
  assert.equal(readFileSync(join(dist, 'index.html'), 'utf8'), 'new page');
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /cleanup interrupted/);
}));

test('real Astro build retains layout CSS through a symlinked workspace ancestor', { timeout: 60000 }, () => {
  const site = resolve(import.meta.dirname, '..');
  const root = mkdtempSync(join(tmpdir(), 'gids-astro-css-'));
  try {
    const actual = resolve(root, 'actual/site'), linked = resolve(root, 'linked/site');
    mkdirSync(resolve(actual, 'src/pages'), { recursive: true });
    mkdirSync(resolve(actual, 'public'));
    for (const name of ['astro.config.mjs', 'package.json', 'tsconfig.json']) cpSync(resolve(site, name), resolve(actual, name));
    symlinkSync(resolve(site, 'node_modules'), resolve(actual, 'node_modules'), 'dir');
    symlinkSync(resolve(root, 'actual'), resolve(root, 'linked'), 'dir');
    writeFileSync(resolve(actual, 'src/pages/index.astro'), `<html><head><title>Asset regression</title></head><body>
      <header class="site-header">GIDS</header><main>Fixture</main>
      <script>document.documentElement.dataset.assetFixture="ready";</script>
      <style is:global>.site-header { height: 73px; display: flex; } body { margin: 0; }</style>
    </body></html>`);
    const env = { ...process.env, GIDS_RESEARCH_BUILD_DIR: '', GIDS_REPORTS_BUILD_DIR: '', GIDS_COUNTRY_BUILD_DIR: '',
      GIDS_CODE_BUILD_DIR: linked, GIDS_ASTRO_RENDER_WORKERS: '2', GIDS_BUILD_PUBLIC_DIR: '',
      GIDS_SITE_BUILD_OUTPUT_DIR: resolve(linked, '.dist-build-test') };
    const result = spawnSync(process.execPath, [resolve(site, 'node_modules/astro/bin/astro.mjs'), 'build'], {
      cwd: actual, env, encoding: 'utf8', timeout: 55000,
    });
    assert.equal(result.status, 0, result.stdout + result.stderr);
    const output = resolve(actual, '.dist-build-test');
    const html = readFileSync(resolve(output, 'index.html'), 'utf8');
    assert.match(html, /<script type="module">[^<]*assetFixture/);
    assert.equal(validateStylesheets(output).styledPages, 1);
    assert.equal(existsSync(resolve(actual, 'dist')), false);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
