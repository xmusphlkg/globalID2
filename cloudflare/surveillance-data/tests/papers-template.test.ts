import assert from 'node:assert/strict';
import { test } from 'node:test';
import { assemblePaper } from '../src/papers-template.ts';

test('assembles public paper fragments without interpreting marker-like article text', () => {
  const html = assemblePaper({ schema: 1, chunks: ['<html><head>', { field: 'head', index: 0 }, '</head><body>', { field: 'main' }, '<a href="/', { field: 'slug' }, '">link</a></body></html>'] },
    { schema: 1, slug: 'paper-1', head: ['<title>Paper</title>'], main: '<main>{{MAIN}} &amp; text</main>' });
  assert.equal(html, '<html><head><title>Paper</title></head><body><main>{{MAIN}} &amp; text</main><a href="/paper-1">link</a></body></html>');
});

test('rejects invalid indexes and unsafe slugs', () => {
  assert.throws(() => assemblePaper({ schema: 1, chunks: [{ field: 'head', index: -1 }] }, { schema: 1, slug: 'paper', head: [], main: '' }));
  assert.throws(() => assemblePaper({ schema: 1, chunks: [] }, { schema: 1, slug: '../paper', head: [], main: '' }));
});
