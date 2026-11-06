import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { openPageAuditCache } from './page-audit-cache.mjs';

test('page facts skip parsing unchanged bytes and reject edits, version changes and damaged cache', () => {
  const root = mkdtempSync(join(tmpdir(), 'page-audit-'));
  try {
    const page = join(root, 'page.html'), cache = join(root, 'cache.json');
    writeFileSync(page, '<main>one</main>');
    let parses = 0;
    const extract = bytes => { parses++; return { text: bytes.toString() }; };
    let facts = openPageAuditCache(cache, 'v1');
    assert.equal(facts.read(page, extract).text, '<main>one</main>'); facts.save();
    facts = openPageAuditCache(cache, 'v1'); facts.read(page, extract); facts.save();
    assert.equal(parses, 1);
    writeFileSync(page, '<main>two</main>');
    facts = openPageAuditCache(cache, 'v1'); assert.equal(facts.read(page, extract).text, '<main>two</main>'); facts.save();
    assert.equal(parses, 2);
    const broken = JSON.parse(readFileSync(cache)); broken.files[page].value = { text: 'corrupt' };
    writeFileSync(cache, JSON.stringify(broken));
    facts = openPageAuditCache(cache, 'v1'); assert.equal(facts.read(page, extract).text, '<main>two</main>'); facts.save();
    facts = openPageAuditCache(cache, 'v2'); facts.read(page, extract);
    assert.equal(parses, 4);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
