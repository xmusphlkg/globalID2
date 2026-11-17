import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { researchTemplateFingerprint } from './research-isolated-cache.mjs';
import { buildResearch, prepareResearchWorkspace } from './build-research.mjs';

test('research builds reject missing styles before saving a cache or publication output', () => {
  const root = mkdtempSync(resolve(tmpdir(), 'gids-research-assets-'));
  try {
    const site = resolve(root, 'site'), snapshot = resolve(root, 'snapshot');
    const work = resolve(root, 'work'), cacheDir = resolve(root, 'article-cache');
    for (const path of ['shared', 'site/src/pages/research', 'site/src/data', 'site/public',
      'snapshot/build/research', 'snapshot/public/research', 'article-cache'])
      mkdirSync(resolve(root, path), { recursive: true });
    writeFileSync(resolve(site, 'src/pages/research/index.astro'), 'template');
    writeFileSync(resolve(snapshot, 'build/research/routes.json'), '{"articles":[{"slug":"paper-a"}]}');
    writeFileSync(resolve(snapshot, 'build/research/index.json'), '{}');
    writeFileSync(resolve(snapshot, 'public/research/index.json'), '{}');
    writeFileSync(resolve(cacheDir, 'current.json'), 'previous verified cache');
    const html = '<a class="skip-link" href="#main-content">Skip</a><main id="main-content">Paper</main>';
    for (const scope of ['full', 'collections', 'shell']) {
      const destination = resolve(root, `output-${scope}`);
      assert.throws(() => buildResearch({ site, project: root, snapshot, work, destination, cacheDir, scope,
        run: (_cmd, _args, opts) => {
          mkdirSync(resolve(opts.cwd, 'dist/research'), { recursive: true });
          writeFileSync(resolve(opts.cwd, 'dist/research/index.html'), html);
          return { status: 0 };
        } }), /Research build assets invalid:.*missing bundled global layout CSS/);
      assert.equal(existsSync(destination), false);
      assert.equal(existsSync(`${destination}.inputs.json`), false);
      assert.equal(readFileSync(resolve(cacheDir, 'current.json'), 'utf8'), 'previous verified cache');
    }
    const destination = resolve(root, 'styled');
    buildResearch({ site, project: root, snapshot, work, destination, scope: 'shell',
      run: (_cmd, _args, opts) => {
        mkdirSync(resolve(opts.cwd, 'dist/research'), { recursive: true });
        writeFileSync(resolve(opts.cwd, 'dist/research/index.html'), '<style>.skip-link{position:fixed}</style>' + html);
        return { status: 0 };
      } });
    assert.equal(existsSync(`${destination}.inputs.json`), true);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

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
    'GIDS_REPORTS_BUILD_DIR', 'GIDS_BUILD_PUBLIC_DIR', 'GIDS_ASTRO_RENDER_WORKERS', 'GIDS_RESEARCH_COLLECTION_RENDER_WORKERS']
    .map(key => [key, process.env[key]]));
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
    process.env.GIDS_BUILD_PUBLIC_DIR = resolve(root, 'unrelated-public');
    process.env.GIDS_ASTRO_RENDER_WORKERS = '0';
    process.env.GIDS_RESEARCH_COLLECTION_RENDER_WORKERS = '2';
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
        assert.equal(opts.env.GIDS_BUILD_PUBLIC_DIR, undefined);
        assert.equal(opts.env.GIDS_ASTRO_RENDER_WORKERS, '2');
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
    assert.equal(process.env.GIDS_ASTRO_RENDER_WORKERS, '0', 'collection override must stay inside the child environment');
    const receipt = JSON.parse(readFileSync(`${destination}.inputs.json`, 'utf8'));
    assert.equal(receipt.scope, 'collections');
    assert.equal(receipt.article_pages_rendered, 0);
    assert.equal(receipt.article_pages_restored, 0);
    assert.equal(receipt.snapshot, snapshot);
    assert.match(receipt.template_fingerprint, /^[a-f0-9]{64}$/);
    assert.match(receipt.pages['research/countries/fr/index.html'], /^[a-f0-9]{64}$/);
    assert.throws(() => buildResearch({ site, project: root, snapshot, work,
      destination: resolve(root, 'invalid'), scope: 'partial' }), /Unknown research build scope/);
    assert.equal(existsSync(resolve(root, 'invalid')), false);
    process.env.GIDS_ASTRO_RENDER_WORKERS = '3';
    for (const [scope, override, expected] of [
      ['collections', undefined, '3'], ['collections', '', '3'], ['collections', '0', '0'],
      ['shell', '2', '2'],
      ['full', '2', '3'], ['full', 'invalid-collection-only-setting', '3'],
    ]) {
      if (override === undefined) delete process.env.GIDS_RESEARCH_COLLECTION_RENDER_WORKERS;
      else process.env.GIDS_RESEARCH_COLLECTION_RENDER_WORKERS = override;
      buildResearch({ site, project: root, snapshot, work, scope,
        destination: resolve(root, `workers-${scope}-${String(override)}`), run: (_cmd, _args, opts) => {
          assert.equal(opts.env.GIDS_ASTRO_RENDER_WORKERS, expected);
          mkdirSync(resolve(opts.cwd, 'dist'), { recursive: true });
          return { status: 0 };
        } });
    }
    for (const invalid of ['-1', '33', '2.5', 'invalid']) {
      process.env.GIDS_RESEARCH_COLLECTION_RENDER_WORKERS = invalid;
      assert.throws(() => buildResearch({ site, project: root, snapshot, work, scope: 'collections',
        destination: resolve(root, 'invalid-workers') }), /GIDS_RESEARCH_COLLECTION_RENDER_WORKERS/);
    }
  } finally {
    for (const [key, value] of Object.entries(inherited)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    rmSync(root, { recursive: true, force: true });
  }
});


test('auto build reuses only an explicitly frozen compatible template baseline', () => {
  const root = mkdtempSync(resolve(tmpdir(), 'gids-research-auto-'));
  try {
    const site = resolve(root, 'site'), snapshot = resolve(root, 'snapshot'), work = resolve(root, 'work');
    for (const path of ['shared', 'site/src/pages/research', 'site/src/data', 'site/public',
      'snapshot/build/research', 'snapshot/public/research']) mkdirSync(resolve(root, path), { recursive: true });
    writeFileSync(resolve(site, 'src/pages/research/index.astro'), 'template');
    writeFileSync(resolve(snapshot, 'build/research/routes.json'), '{"articles":[{"slug":"paper-a"}]}');
    writeFileSync(resolve(snapshot, 'build/research/index.json'), '{}');
    writeFileSync(resolve(snapshot, 'public/research/index.json'), '{}');
    writeFileSync(resolve(snapshot, 'article-inputs.json'), '{"schema":1,"articles":{}}');
    writeFileSync(resolve(snapshot, 'public/research/catalogue.json'), '{}');
    const candidate = resolve(root, 'candidate.json'), projectionBaseline = resolve(root, 'baseline.json');
    writeFileSync(candidate, '{}');
    const fingerprint = researchTemplateFingerprint({ site, project: root });
    const valid = { schema: 1, template_fingerprint: fingerprint, candidate, generation: 'a'.repeat(64) };
    for (const [index, baseline, scope] of [
      [0, valid, 'collections'], [1, { ...valid, template_fingerprint: 'b'.repeat(64) }, 'shell'],
      [2, { ...valid, candidate: resolve(root, 'missing.json') }, 'full'],
      [3, { schema: 1, mode: 'full' }, 'full'],
    ]) {
      writeFileSync(projectionBaseline, JSON.stringify(baseline));
      const destination = resolve(root, `output-${index}`);
      buildResearch({ site, project: root, snapshot, destination, work, scope: 'auto', projectionBaseline,
        run: (_cmd, _args, opts) => {
          assert.equal(opts.env.GIDS_RESEARCH_BUILD_SCOPE, scope);
          if (scope === 'shell') {
            assert.deepEqual(JSON.parse(readFileSync(opts.env.GIDS_RESEARCH_BUILD_PLAN, 'utf8')), { selected: ['paper-a'] });
          }
          mkdirSync(resolve(opts.cwd, 'dist/research'), { recursive: true });
          writeFileSync(resolve(opts.cwd, 'dist/research/index.html'), 'current collection');
          return { status: 0 };
        } });
      const receipt = JSON.parse(readFileSync(`${destination}.inputs.json`, 'utf8'));
      assert.equal(receipt.scope, scope);
      assert.equal(receipt.template_fingerprint, fingerprint);
      if (scope === 'collections' || scope === 'shell') {
        assert.equal(receipt.projection_baseline, candidate);
        for (const key of ['article_inputs_sha256', 'routes_sha256', 'research_index_sha256',
          'public_research_index_sha256', 'public_research_catalogue_sha256'])
          assert.match(receipt[key], /^[a-f0-9]{64}$/);
      }
      if (scope === 'shell') assert.equal(receipt.shell_slug, 'paper-a');
    }
    writeFileSync(resolve(snapshot, 'build/research/routes.json'), '{"articles":[]}');
    assert.throws(() => buildResearch({ site, project: root, snapshot, destination: resolve(root, 'empty-shell'),
      work, scope: 'shell' }), /one published article/);
    // Article membership/metadata are snapshot inputs, never template identity.
    mkdirSync(resolve(site, 'src/data/research'), { recursive: true });
    writeFileSync(resolve(site, 'src/data/research/routes.json'), '{"articles":[{"slug":"new"}]}');
    assert.equal(researchTemplateFingerprint({ site, project: root }), fingerprint);
    writeFileSync(resolve(site, 'src/data/meta.json'), '{"site_name":"changed shell"}');
    assert.notEqual(researchTemplateFingerprint({ site, project: root }), fingerprint);
    rmSync(resolve(site, 'src/data/meta.json'));
    writeFileSync(resolve(site, 'src/pages/research/index.astro'), 'changed template');
    assert.notEqual(researchTemplateFingerprint({ site, project: root }), fingerprint);
    assert.throws(() => buildResearch({ site, project: root, snapshot, destination: resolve(root, 'drift'),
      work, scope: 'full', run: (_cmd, _args, opts) => {
        mkdirSync(resolve(opts.cwd, 'dist'), { recursive: true });
        writeFileSync(resolve(site, 'src/pages/research/index.astro'), 'mid-build change');
        return { status: 0 };
      } }), /template inputs changed/);
    assert.equal(existsSync(resolve(root, 'drift.inputs.json')), false);
    assert.throws(() => buildResearch({ site, project: root, snapshot, destination: resolve(root, 'snapshot-drift'),
      work, scope: 'collections', run: (_cmd, _args, opts) => {
        mkdirSync(resolve(opts.cwd, 'dist'), { recursive: true });
        writeFileSync(resolve(snapshot, 'public/research/index.json'), '{"changed":true}');
        return { status: 0 };
      } }), /snapshot inputs changed/);
    assert.equal(existsSync(resolve(root, 'snapshot-drift.inputs.json')), false);

  } finally { rmSync(root, { recursive: true, force: true }); }
});
