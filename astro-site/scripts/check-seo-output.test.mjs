import assert from 'node:assert/strict';
import test from 'node:test';

import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { auditSeoOutput, redirectFor, tags } from './check-seo-output.mjs';

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
