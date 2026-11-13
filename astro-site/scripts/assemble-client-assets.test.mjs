import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { test } from 'node:test';
import { countryBaselineDigest } from './build-country-template.mjs';
import { assembleClientAssets } from './assemble-client-assets.mjs';

const put = (root, path, body) => { mkdirSync(dirname(join(root, path)), { recursive: true }); writeFileSync(join(root, path), body); };
function fixture(run) {
  const root = mkdtempSync(join(tmpdir(), 'gids-client-assets-'));
  const baseline = join(root, 'old'), compiled = join(root, 'compiled'), destination = join(root, 'candidate');
  try {
    put(baseline, 'index.html', '<h1>Published text</h1><astro-island props="unchanged" component-url="/_astro/View.old.js"></astro-island>');
    put(baseline, '_astro/View.old.js', 'old client'); put(baseline, 'data.json', 'published data');
    put(baseline, 'release.json', JSON.stringify({ release_id: 'old', build_digest: countryBaselineDigest(baseline) }));
    put(compiled, 'index.html', '<h1>Unpublished source must not leak</h1>');
    put(compiled, '_astro/View.new.js', 'new client');
    run({ baseline, compiled, destination });
  } finally { rmSync(root, { recursive: true, force: true }); }
}

test('accepts only unique asset URLs while retaining published HTML and resources', () => fixture(paths => {
  const receipt = assembleClientAssets(paths);
  assert.equal(readFileSync(join(paths.destination, 'index.html'), 'utf8'), '<h1>Published text</h1><astro-island props="unchanged" component-url="/_astro/View.new.js"></astro-island>');
  assert.equal(readFileSync(join(paths.destination, '_astro/View.old.js'), 'utf8'), 'old client');
  assert.equal(readFileSync(join(paths.destination, 'data.json'), 'utf8'), 'published data');
  assert.equal(receipt.changed_asset_references, 1);
  assert.equal(receipt.deployment_ready, false);
}));

test('rejects duplicate logical entries, immutable collisions and corrupted baselines', () => {
  fixture(paths => { put(paths.compiled, '_astro/View.other.js', 'other'); assert.throws(() => assembleClientAssets(paths), /uniquely map/); });
  fixture(paths => { put(paths.compiled, '_astro/View.old.js', 'different'); rmSync(join(paths.compiled, '_astro/View.new.js')); assert.throws(() => assembleClientAssets(paths), /collision/); });
  fixture(paths => { put(paths.baseline, 'data.json', 'unpublished'); assert.throws(() => assembleClientAssets(paths), /release digest/); });
});
