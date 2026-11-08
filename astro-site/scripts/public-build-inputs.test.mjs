import assert from 'node:assert/strict';
import { existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { isSurveillancePublicPath, preparePublicBuildInputs } from './public-build-inputs.mjs';

test('external public staging excludes surveillance while preserving source files and independent content', () => {
  const root = mkdtempSync(join(tmpdir(), 'gids-public-stage-'));
  try {
    const publicRoot = join(root, 'public'); const stageRoot = join(root, 'private');
    const excluded = ['site-data/countries/jp.json', 'site-data/diseases/d038.json', 'site-data/meta.json', 'site-data/site-status.json', 'site-data/current.json', 'site-data/objects/a.json'];
    const kept = ['site-data/research/shards/one.json', 'site-data/reports/index.json', 'flags/jp.svg', 'data/world.json', 'robots.txt'];
    for (const name of [...excluded, ...kept]) { const path = join(publicRoot, name); mkdirSync(dirname(path), { recursive: true }); writeFileSync(path, name); }
    const legacy = preparePublicBuildInputs({ publicRoot, stageRoot });
    assert.equal(legacy.publicDir, publicRoot); assert.equal(legacy.staged, false); assert.equal(existsSync(stageRoot), false);
    const staged = preparePublicBuildInputs({ publicRoot, stageRoot, external: true });
    for (const name of excluded) { assert.equal(existsSync(join(staged.publicDir, name)), false); assert.equal(readFileSync(join(publicRoot, name), 'utf8'), name); }
    for (const name of kept) assert.equal(readFileSync(join(staged.publicDir, name), 'utf8'), name);
    assert.equal(lstatSync(join(staged.publicDir, 'site-data/research')).isSymbolicLink(), true);
    staged.cleanup(); assert.equal(existsSync(stageRoot), false);
    for (const name of [...excluded, ...kept]) assert.equal(existsSync(join(publicRoot, name)), true);
    assert.throws(() => preparePublicBuildInputs({ publicRoot, stageRoot: join(publicRoot, 'nested'), external: true }), /separate/);
    assert.equal(isSurveillancePublicPath('site-data/research/countries/jp.json'), false);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
