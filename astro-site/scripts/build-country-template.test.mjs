import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import { mergeCountryOutput, selectedCountryPaths } from './build-country-template.mjs';

const write = (root, path, body) => { mkdirSync(dirname(join(root, path)), { recursive: true }); writeFileSync(join(root, path), body); };
const metadata = { countries: [{ code: 'JP' }, { code: 'CA' }] };

test('country scope expands exactly three locales and rejects missing or unsafe identities', () => {
  assert.deepEqual(selectedCountryPaths(metadata, ['JP']).paths, ['countries/jp/index.html', 'zh/countries/jp/index.html', 'fr/countries/jp/index.html']);
  assert.equal(selectedCountryPaths(metadata).paths.length, 6);
  assert.throws(() => selectedCountryPaths(metadata, ['../jp']), /Unknown/);
  assert.throws(() => selectedCountryPaths(metadata, []), /empty/);
  assert.throws(() => selectedCountryPaths(metadata, ['US']), /Unknown/);
});

test('isolated country output preserves unrelated HTML and retained assets byte for byte', () => {
  const root = mkdtempSync(join(tmpdir(), 'gids-country-'));
  try {
    const baseline = join(root, 'baseline'), output = join(root, 'output'), destination = join(root, 'candidate');
    const paths = selectedCountryPaths(metadata, ['jp']).paths;
    for (const path of paths) {
      write(baseline, path, '<html>old country</html>');
      write(output, path, '<html><script src="/_astro/new.js"></script>new country</html>');
    }
    write(baseline, 'research/articles/paper/index.html', '<article>preserved paper</article>');
    write(baseline, 'reports/jp/index.html', '<article>preserved report</article>');
    write(baseline, 'index.html', '<html>preserved home</html>');
    write(baseline, '_astro/old.js', 'old();');
    write(baseline, 'release.json', '{"release_id":"baseline"}');
    write(output, '_astro/new.js', 'newCode();');
    const result = mergeCountryOutput({ baseline, output, destination, expectedPaths: paths });
    assert.equal(result.html_assembly_count, 3);
    assert.equal(result.preserved_html_count, 3);
    assert.equal(result.new_asset_count, 1);
    for (const path of ['research/articles/paper/index.html', 'reports/jp/index.html', 'index.html', '_astro/old.js', 'release.json']) {
      assert.deepEqual(readFileSync(join(destination, path)), readFileSync(join(baseline, path)));
    }
    assert.equal(readFileSync(join(baseline, paths[0]), 'utf8'), '<html>old country</html>');
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('unexpected routes, new routes, asset collisions and broken references cannot produce a valid candidate', () => {
  for (const failure of ['unexpected', 'new-route', 'collision', 'missing-asset']) {
    const root = mkdtempSync(join(tmpdir(), 'gids-country-reject-'));
    try {
      const baseline = join(root, 'baseline'), output = join(root, 'output'), destination = join(root, 'candidate');
      const path = 'countries/jp/index.html';
      write(baseline, 'index.html', 'home');
      if (failure !== 'new-route') write(baseline, path, 'country');
      write(output, path, failure === 'missing-asset' ? '<script src="/_astro/missing.js"></script>' : 'new country');
      if (failure === 'unexpected') write(output, 'reports/index.html', 'must not render');
      if (failure === 'collision') { write(baseline, '_astro/shared.js', 'old'); write(output, '_astro/shared.js', 'different'); }
      assert.throws(() => mergeCountryOutput({ baseline, output, destination, expectedPaths: [path] }), /routing publication|route set|collision|missing asset/);
    } finally { rmSync(root, { recursive: true, force: true }); }
  }
});

test('baseline digest binds unchanged release IDs to all artifact bytes', async () => {
  const { countryBaselineDigest } = await import('./build-country-template.mjs');
  const root = mkdtempSync(join(tmpdir(), 'gids-country-baseline-'));
  try {
    write(root, 'index.html', 'home'); write(root, 'release.json', '{"release_id":"same"}');
    const first = countryBaselineDigest(root);
    write(root, '_astro/shared.js', 'one');
    assert.notEqual(countryBaselineDigest(root), first);
    const second = countryBaselineDigest(root);
    write(root, 'release.json', '{"release_id":"updated-provenance"}');
    assert.equal(countryBaselineDigest(root), second);
    write(root, '_astro/shared.js', 'two');
    assert.notEqual(countryBaselineDigest(root), second);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('baseline digest matches Python release pipeline path-component ordering', async () => {
  const { countryBaselineDigest } = await import('./build-country-template.mjs');
  const root = mkdtempSync(join(tmpdir(), 'gids-country-python-digest-'));
  try {
    for (const [path, body] of [['_astro/main.js', 'js'], ['a/z.html', 'child'], ['a-b.html', 'sibling'], ['index.html', 'home']]) write(root, path, body);
    write(root, 'release.json', '{}');
    // Computed with the Python production pipeline's sorted(Path) algorithm.
    assert.equal(countryBaselineDigest(root), '1a655a958bf5077859eb6d75ecadc5c7c9b5c881409a499f7d4af10c7ef1f999');
  } finally { rmSync(root, { recursive: true, force: true }); }
});
