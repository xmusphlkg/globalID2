import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { buildSiteResearchCollections, expectedCollectionPaths } from './build-site-research-collections.mjs';

function fixture(root) {
  const site = resolve(root, 'site'), pub = resolve(site, 'public/site-data/research');
  const put = (file, body) => { mkdirSync(dirname(file), { recursive: true }); writeFileSync(file, body); };
  put(resolve(site, 'src/data/research/index.json'), '{"frozen":"private"}');
  put(resolve(pub, 'shards/library.json'), JSON.stringify({ records: [{
    article_id: 'library-only', title: 'Indexed paper', peer_review_status: 'peer_reviewed',
    diseases: [{ disease_id: 'D038', slug: 'influenza', name_en: 'Influenza' }],
  }] }));
  put(resolve(pub, 'index.json'), JSON.stringify({
    facets: { diseases: [], countries: [{ slug: 'jp' }], topics: [{ slug: 'surveillance' }], weeks: [{ week: '2026-W46' }] },
    bibliographic_articles: { sharded: true, kind: 'list', chunks: [{ file: 'shards/library.json' }] },
    preprints: [{ peer_review_status: 'preprint', editorial_status: 'draft', diseases: [{ disease_id: 'hidden', slug: 'hidden' }] }],
  }));
  put(resolve(site, 'dist/research/collections/retired/index.html'), 'old page');
  put(resolve(site, 'dist/index.html'), 'main page');
  return { site, pub, put };
}

for (const missing of [false, true]) test(`template research collections use current shards and validate before merging (missing=${missing})`, () => {
  const root = mkdtempSync(resolve(tmpdir(), 'gids-site-collections-'));
  try {
    const { site, pub, put } = fixture(root);
    const paths = expectedCollectionPaths(pub);
    assert.equal(paths.length, 12);
    assert.ok(paths.includes('fr/research/diseases/influenza/index.html'), 'bibliographic-only disease must render');
    assert.ok(!paths.some(path => path.includes('/hidden/')));
    const run = () => buildSiteResearchCollections({ site, build: ({ snapshot, destination, scope }) => {
      assert.equal(scope, 'collections');
      assert.equal(readFileSync(resolve(snapshot, 'build/research/index.json'), 'utf8'), '{"frozen":"private"}');
      for (const path of missing ? paths.slice(1) : paths) put(resolve(destination, path), `<h1>Current ${path}</h1>`);
      for (const prefix of ['', 'zh/', 'fr/']) put(resolve(destination, prefix, 'research/index.html'), 'current landing');
      put(resolve(destination, '_astro/new.js'), 'new component');
      put(`${destination}.inputs.json`, JSON.stringify({ scope: 'collections', article_pages_rendered: 0 }));
    } });
    if (missing) {
      assert.throws(run, /omitted current route/);
      assert.equal(readFileSync(resolve(site, 'dist/research/collections/retired/index.html'), 'utf8'), 'old page');
      assert.equal(existsSync(resolve(site, 'dist/research-collections-build.json')), false);
    } else {
      const receipt = run();
      assert.deepEqual(Object.keys(receipt.pages), paths);
      assert.equal(existsSync(resolve(site, 'dist/research/collections/retired')), false);
      assert.equal(readFileSync(resolve(site, 'dist/index.html'), 'utf8'), 'main page');
      assert.equal(readFileSync(resolve(site, 'dist/_astro/new.js'), 'utf8'), 'new component');
      assert.equal(existsSync(resolve(site, 'dist/research/articles')), false);
      for (const path of paths) assert.match(readFileSync(resolve(site, 'dist', path), 'utf8'), /^<h1>Current/);
    }
  } finally { rmSync(root, { recursive: true, force: true }); }
});
