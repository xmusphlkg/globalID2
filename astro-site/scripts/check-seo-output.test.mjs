import assert from 'node:assert/strict';
import test from 'node:test';
import { spawnSync } from 'node:child_process';

import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { auditSeoParallel } from './seo-audit-workers.mjs';

import { auditSeoOutput, redirectFor, tags } from './check-seo-output.mjs';

test('catalogue uniqueness checks do not retain entire HTML documents', () => {
  const dist = mkdtempSync(join(tmpdir(), 'seo-bounded-heap-'));
  try {
    const padding = 'x'.repeat(1024 * 1024);
    for (let i = 0; i < 96; i++) {
      const directory = join(dist, String(i));
      mkdirSync(directory);
      writeFileSync(join(directory, 'index.html'), `<html><head><title>Unique catalogue page ${i}</title>
        <meta name="description" content="A catalogue entry">
        <link rel="canonical" href="https://globalinfectiousdisease.com/${i}/">
        <link rel="alternate" hreflang="en"><link rel="alternate" hreflang="fr-FR">
        <link rel="alternate" hreflang="zh-CN"><link rel="alternate" hreflang="x-default">
        <meta property="og:image" content="/card.png"><meta property="og:image:alt" content="Card">
        <meta name="twitter:image:alt" content="Card"></head><body><main><h1>Entry ${i}</h1><p>${padding}</p></main></body></html>`);
    }
    const script = `import {auditSeoOutput} from ${JSON.stringify(new URL('./check-seo-output.mjs', import.meta.url).href)};
      const result=auditSeoOutput(process.argv[1]);
      if(!result.passed || result.pages!==96)throw new Error(JSON.stringify(result));`;
    const result = spawnSync(process.execPath, ['--max-old-space-size=64', '--input-type=module', '-e', script, dist],
      { encoding: 'utf8', timeout: 30000 });
    assert.equal(result.status, 0, result.stderr || String(result.error));
  } finally { rmSync(dist, { recursive: true, force: true }); }
});

test('parses greater-than signs inside quoted meta content', () => {
  const html = '<meta name="description" content="Resistance exceeded 10%, including >5% in one group.">';

  const [description] = tags(html, 'meta');

  assert.equal(description.name, 'description');
  assert.equal(description.content, 'Resistance exceeded 10%, including >5% in one group.');
});

test('wildcard redirect rules match by prefix and exact rules still win', () => {
  const redirects = new Map([
    ['/research/articles/*', 'https://papers.example/research/articles/:splat'],
    ['/old/', '/new/'],
  ]);

  assert.equal(redirectFor('/old/', redirects), '/new/');
  assert.equal(redirectFor('/research/articles/a-b/', redirects), 'https://papers.example/research/articles/:splat');
  assert.equal(redirectFor('/research/', redirects), undefined);
});

function fixtureDist(sitemapLoc) {
  const dist = mkdtempSync(join(tmpdir(), 'seo-'));
  mkdirSync(join(dist, 'sitemaps'));
  writeFileSync(join(dist, '_redirects'), '/research/articles/*  https://papers.example/research/articles/:splat  301\n');
  writeFileSync(join(dist, 'sitemaps', 'a.xml'), `<urlset><url><loc>${sitemapLoc}</loc></url></urlset>`);
  return dist;
}

test('sitemap URLs on the papers host are not looked up in dist', () => {
  const dist = fixtureDist('https://papers.example/research/articles/a/');
  try {
    const { errors } = auditSeoOutput(dist);
    assert.deepEqual(errors.filter(error => error.includes('sitemap')), []);
  } finally { rmSync(dist, { recursive: true, force: true }); }
});

test('reports directory is on the reports R2 host, outside the main Pages bundle', () => {
  const dist = fixtureDist('https://reports.example/reports/');
  try {
    const result = auditSeoOutput(dist);
    assert.equal(result.pages, 0);
    assert.deepEqual(result.errors, []);
  } finally { rmSync(dist, { recursive: true, force: true }); }
});

test('main-origin sitemap URLs that redirect to the papers host are reported', () => {
  const dist = fixtureDist('https://globalinfectiousdisease.com/research/articles/a/');
  try {
    const { errors } = auditSeoOutput(dist);
    assert.ok(errors.some(error => error.includes('redirects to another host')));
  } finally { rmSync(dist, { recursive: true, force: true }); }
});

function pageDist(pages) {
  const dist = mkdtempSync(join(tmpdir(), 'seo-'));
  writeFileSync(join(dist, '_redirects'), '/research/articles/*  https://papers.example/research/articles/:splat  301\n');
  for (const [route, html] of Object.entries(pages)) {
    const dir = join(dist, route);
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, 'index.html'), html);
  }
  return dist;
}

const linkErrors = errors => errors.filter(error => /redirect|broken internal link/.test(error));

test('independent research routes are excluded while main-site links remain valid', () => {
  const dist = pageDist({
    'research/weekly/w1': '<a href="/research/articles/missing/">article</a>',
    'about': '<a href="/research/articles/missing/">research</a>',
  });
  try {
    const result = auditSeoOutput(dist, { independentResearch: true });
    assert.equal(result.pages, 1);
    assert.deepEqual(linkErrors(result.errors), []);
  } finally { rmSync(dist, { recursive: true, force: true }); }
});

test('links between papers pages share one host and are not flagged as redirects', () => {
  const dist = pageDist({
    'research/articles/a': '<a href="/research/articles/b/">b</a>',
    'research/articles/b': '<p>b</p>',
  });
  try {
    assert.deepEqual(linkErrors(auditSeoOutput(dist).errors), []);
  } finally { rmSync(dist, { recursive: true, force: true }); }
});

test('main-site pages linking to a papers path through the redirect are reported', () => {
  const dist = pageDist({
    'research/weekly/w1': '<a href="/research/articles/a/">a</a>',
    'research/articles/a': '<p>a</p>',
  });
  try {
    const errors = linkErrors(auditSeoOutput(dist).errors);
    assert.equal(errors.length, 1);
    assert.match(errors[0], /^\/research\/weekly\/w1\/: internal link points through redirect/);
  } finally { rmSync(dist, { recursive: true, force: true }); }
});

test('main-site pages linking to the absolute papers URL are accepted', () => {
  const dist = pageDist({
    'research/weekly/w1': '<a href="https://papers.example/research/articles/a/">a</a>',
  });
  try {
    assert.deepEqual(linkErrors(auditSeoOutput(dist).errors), []);
  } finally { rmSync(dist, { recursive: true, force: true }); }
});

test('link lookup cache does not survive another audit',()=>{
  const dist=pageDist({'':'<a href="/target/">target</a>',target:'<p>target</p>'});
  try {
    assert.deepEqual(linkErrors(auditSeoOutput(dist).errors),[]);
    rmSync(join(dist,'target'),{recursive:true});
    assert.ok(linkErrors(auditSeoOutput(dist).errors).some(e=>e.includes('broken internal link /target/')));
  } finally {rmSync(dist,{recursive:true,force:true});}
});


test('parallel audit preserves global uniqueness, sitemap membership and cross-worker links', async () => {
  const html = title => `<html><head><title>${title}</title><meta name="description" content="Entry">
    <link rel="canonical" href="https://globalinfectiousdisease.com/same/">
    <link rel="alternate" hreflang="en"><link rel="alternate" hreflang="fr-FR">
    <link rel="alternate" hreflang="zh-CN"><link rel="alternate" hreflang="x-default">
    <meta property="og:image" content="/card.png"><meta property="og:image:alt" content="Card">
    <meta name="twitter:image:alt" content="Card"></head><body><main><h1>Entry</h1>
    <a href="/b/">Across workers</a></main></body></html>`;
  const dist = pageDist({ a: html('Duplicate'), b: html('Duplicate') });
  mkdirSync(join(dist, 'sitemaps'));
  writeFileSync(join(dist, 'sitemaps', 'a.xml'), '<urlset><url><loc>https://globalinfectiousdisease.com/b/</loc></url></urlset>');
  try {
    const serial = auditSeoOutput(dist);
    const parallel = await auditSeoParallel(dist, { workers: 2 });
    assert.deepEqual(parallel, serial);
    assert.equal(parallel.errors.length, 2);
    assert.ok(parallel.errors.some(error => error.includes('duplicate indexable title')));
    assert.ok(parallel.errors.some(error => error.includes('duplicate canonical')));
    writeFileSync(join(dist, 'b', 'index.html'), html('Unique').replace('/same/', '/b/'));
    assert.equal((await auditSeoParallel(dist, { workers: 2 })).passed, true);
    writeFileSync(join(dist, 'b', 'index.html'), html('Unique').replace('<head>', '<head><meta name="robots" content="noindex">'));
    assert.deepEqual(await auditSeoParallel(dist, { workers: 2 }), auditSeoOutput(dist));
    assert.ok((await auditSeoParallel(dist, { workers: 2 })).errors.some(error => error.includes('sitemap includes noindex')));
    writeFileSync(join(dist, 'b', 'index.html'), '<meta content="&#x110000;">');
    await assert.rejects(auditSeoParallel(dist, { workers: 2 }), /code point/i);
    for (const workers of [0, 17, 1.5, NaN]) await assert.rejects(auditSeoParallel(dist, { workers }));
  } finally { rmSync(dist, { recursive: true, force: true }); }
});
