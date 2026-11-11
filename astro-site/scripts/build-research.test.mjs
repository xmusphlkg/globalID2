import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
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
    assert.equal(JSON.parse(readFileSync(resolve(destination, 'site-data/research/index.json'), 'utf8')).version, 'public-snapshot');
    assert.equal(JSON.parse(readFileSync(resolve(site, 'src/data/research/index.json'), 'utf8')).version, 'main');
    assert.throws(() => buildResearch({ site, project: root, snapshot, work, destination: resolve(root, 'failed'), run: () => ({ status: 1 }) }), /build failed/);
    assert.throws(() => prepareResearchWorkspace({ site, snapshot, work: root }), /must be separate/);
    assert.equal(JSON.parse(readFileSync(resolve(site, 'src/data/research/index.json'), 'utf8')).version, 'main');
  } finally { rmSync(root, { recursive: true, force: true }); }
});
