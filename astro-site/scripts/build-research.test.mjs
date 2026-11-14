import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { buildResearch, prepareResearchWorkspace } from './build-research.mjs';

test('isolated research build reads its own snapshot without writing main site data', () => {
  const root = mkdtempSync(resolve(tmpdir(), 'gids-research-build-'));
  try {
    const site = resolve(root, 'site'), snapshot = resolve(root, 'snapshot');
    mkdirSync(resolve(root, 'shared'), { recursive: true });
    writeFileSync(resolve(root, 'shared/research-publishing.json'), '{}');
    for (const path of ['src/data/research', 'src/pages/research', 'src/lib', 'public', 'node_modules/.bin']) mkdirSync(resolve(site, path), { recursive: true });
    mkdirSync(resolve(snapshot, 'build/research'), { recursive: true });
    mkdirSync(resolve(snapshot, 'public/research'), { recursive: true });
    writeFileSync(resolve(site, 'src/data/meta.json'), '{"version":"main"}');
    writeFileSync(resolve(site, 'src/data/research/index.json'), '{"version":"main"}');
    writeFileSync(resolve(site, 'src/pages/research/index.astro'), 'research');
    writeFileSync(resolve(site, 'src/lib/site.ts'), 'site');
    writeFileSync(resolve(snapshot, 'build/research/routes.json'), '{}');
    writeFileSync(resolve(snapshot, 'build/research/index.json'), '{"version":"snapshot"}');
    writeFileSync(resolve(snapshot, 'public/research/index.json'), '{"version":"public-snapshot"}');
    const work = resolve(root, 'work'), destination = resolve(root, 'output');
    buildResearch({ site, project: root, snapshot, work, destination, run: (_cmd, _args, opts) => {
      assert.equal(opts.cwd, resolve(work, 'site'));
      assert.equal(opts.env.GIDS_SITE_DATA_ROOT, resolve(work, 'site/src/data'));
      assert.equal(JSON.parse(readFileSync(resolve(opts.env.GIDS_SITE_DATA_ROOT, 'research/index.json'), 'utf8')).version, 'snapshot');
      mkdirSync(resolve(work, 'site/dist'), { recursive: true });
      writeFileSync(resolve(work, 'site/dist/index.html'), 'rendered');
      return { status: 0 };
    } });
    assert.equal(readFileSync(resolve(destination, 'index.html'), 'utf8'), 'rendered');
    assert.equal(existsSync(resolve(destination, 'site-data/research/index.json')), false);
    assert.equal(JSON.parse(readFileSync(resolve(site, 'src/data/research/index.json'), 'utf8')).version, 'main');
    const oldMode = process.env.GIDS_RESEARCH_STATIC_HOST;
    try {
      process.env.GIDS_RESEARCH_STATIC_HOST = '1';
      buildResearch({ site, project: root, snapshot, work, destination: resolve(root, 'static-output'), run: (_cmd, _args, opts) => {
        assert.equal(opts.env.PUBLIC_GIDS_RESEARCH_MAIN_ORIGIN, '0');
        mkdirSync(resolve(work, 'site/dist'), { recursive: true });
        writeFileSync(resolve(work, 'site/dist/index.html'), 'static');
        return { status: 0 };
      } });
    } finally {
      if (oldMode === undefined) delete process.env.GIDS_RESEARCH_STATIC_HOST;
      else process.env.GIDS_RESEARCH_STATIC_HOST = oldMode;
    }
    assert.throws(() => buildResearch({ site, project: root, snapshot, work, destination: resolve(root, 'failed'), run: () => ({ status: 1 }) }), /build failed/);
    assert.throws(() => prepareResearchWorkspace({ site, snapshot, work: root }), /must be separate/);
    assert.equal(JSON.parse(readFileSync(resolve(site, 'src/data/research/index.json'), 'utf8')).version, 'main');
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('collection rebuild skips articles without reading or replacing the complete article cache', () => {
  const root = mkdtempSync(resolve(tmpdir(), 'gids-research-collections-'));
  const inherited = Object.fromEntries(['GIDS_RESEARCH_BUILD_PLAN', 'GIDS_PAGE_BUILD_PLAN',
    'GIDS_RESEARCH_PUBLICATION_ENABLED', 'GIDS_COUNTRY_BUILD_DIR', 'GIDS_CODE_BUILD_DIR',
    'GIDS_REPORTS_BUILD_DIR'].map(key => [key, process.env[key]]));
  try {
    const site = resolve(root, 'site'), snapshot = resolve(root, 'snapshot');
    const work = resolve(root, 'work'), destination = resolve(root, 'output'), cacheDir = resolve(root, 'article-cache');
    mkdirSync(resolve(root, 'shared'), { recursive: true });
    for (const path of ['src/data/research', 'src/pages/research/countries', 'public', 'node_modules/.bin'])
      mkdirSync(resolve(site, path), { recursive: true });
    mkdirSync(resolve(snapshot, 'build/research'), { recursive: true });
    mkdirSync(resolve(snapshot, 'public/research'), { recursive: true });
    writeFileSync(resolve(site, 'src/pages/research/countries/[code].astro'), 'current collection template');
    writeFileSync(resolve(site, 'src/data/research/index.json'), '{"version":"main"}');
    writeFileSync(resolve(snapshot, 'build/research/routes.json'), '{"articles":[{"slug":"existing-article"}]}');
    writeFileSync(resolve(snapshot, 'build/research/index.json'), '{"version":"frozen"}');
    writeFileSync(resolve(snapshot, 'public/research/index.json'), '{"version":"frozen"}');
    // Collections must not even decode article inputs or consult a previous
    // article HTML cache. Such a cache may be missing during v3 publication.
    writeFileSync(resolve(snapshot, 'article-inputs.json'), 'invalid article inputs');
    mkdirSync(cacheDir);
    writeFileSync(resolve(cacheDir, 'current.json'), 'existing full cache pointer');
    process.env.GIDS_RESEARCH_BUILD_PLAN = resolve(root, 'stale-article-plan.json');
    process.env.GIDS_PAGE_BUILD_PLAN = resolve(root, 'stale-page-plan.json');
    process.env.GIDS_RESEARCH_PUBLICATION_ENABLED = '1';
    process.env.GIDS_CODE_BUILD_DIR = resolve(root, 'outer-code-build');
    process.env.GIDS_COUNTRY_BUILD_DIR = resolve(root, 'outer-country-build');
    process.env.GIDS_REPORTS_BUILD_DIR = resolve(root, 'outer-report-build');
    let renderCount = 0;
    buildResearch({ site, project: root, snapshot, work, destination, cacheDir, scope: 'collections',
      run: (_cmd, _args, opts) => {
        renderCount++;
        assert.equal(opts.env.GIDS_RESEARCH_BUILD_SCOPE, 'collections');
        assert.equal(opts.env.GIDS_RESEARCH_BUILD_DIR, resolve(work, 'site'));
        assert.equal(opts.env.GIDS_PAGE_BUILD_PLAN, undefined);
        assert.equal(opts.env.GIDS_CODE_BUILD_DIR, undefined);
        assert.equal(opts.env.GIDS_COUNTRY_BUILD_DIR, undefined);
        assert.equal(opts.env.GIDS_REPORTS_BUILD_DIR, undefined);
        assert.deepEqual(JSON.parse(readFileSync(opts.env.GIDS_RESEARCH_BUILD_PLAN, 'utf8')), { selected: [] });
        assert.equal(readFileSync(resolve(opts.cwd, 'src/pages/research/countries/[code].astro'), 'utf8'), 'current collection template');
        assert.equal(JSON.parse(readFileSync(resolve(opts.env.GIDS_SITE_DATA_ROOT, 'research/index.json'), 'utf8')).version, 'frozen');
        mkdirSync(resolve(opts.cwd, 'dist/research/countries/fr'), { recursive: true });
        writeFileSync(resolve(opts.cwd, 'dist/research/countries/fr/index.html'), 'newly rendered collection');
        return { status: 0 };
      } });
    assert.equal(renderCount, 1, 'partial build must never fall back to a complete article render');
    assert.equal(readFileSync(resolve(destination, 'research/countries/fr/index.html'), 'utf8'), 'newly rendered collection');
    assert.equal(existsSync(resolve(destination, 'research/articles')), false);
    assert.equal(readFileSync(resolve(cacheDir, 'current.json'), 'utf8'), 'existing full cache pointer');
    assert.deepEqual(JSON.parse(readFileSync(`${destination}.inputs.json`, 'utf8')), {
      schema: 1, scope: 'collections', article_pages_rendered: 0, snapshot,
    });
    assert.throws(() => buildResearch({ site, project: root, snapshot, work,
      destination: resolve(root, 'invalid'), scope: 'partial' }), /Unknown research build scope/);
    assert.equal(existsSync(resolve(root, 'invalid')), false);
  } finally {
    for (const [key, value] of Object.entries(inherited)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    rmSync(root, { recursive: true, force: true });
  }
});
