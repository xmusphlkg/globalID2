import { strict as assert } from 'node:assert';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, test } from 'node:test';
import {
  INTERNATIONAL_SCHEMA_VERSION,
  readInternationalByCountry,
  readInternationalForCountryPage,
  recentNotices,
} from './international.ts';

// Fixture shards keep these tests independent of whichever notices the last
// export happened to contain.
const dir = mkdtempSync(join(tmpdir(), 'intl-country-'));
after(() => rmSync(dir, { recursive: true, force: true }));

function notice(id: string, published: string) {
  return { event_id: id, published_date: published, diseases: [], countries: [] };
}

function shard(code: string, events: ReturnType<typeof notice>[]) {
  writeFileSync(
    join(dir, `${code}.json`),
    JSON.stringify({ schema_version: INTERNATIONAL_SCHEMA_VERSION, iso_years: ['2026'], event_count: events.length, events }),
  );
}

shard('UG', [notice('who_don:ug-1', '2026-10-01')]);
shard('US', [notice('who_don:us-1', '2026-09-01')]);
shard('CD', [notice('who_don:cd-1', '2026-08-01'), notice('who_don:cd-2', '2026-10-20'), notice('who_don:cd-3', '2026-09-15')]);

test('a country page slug resolves to the ISO-keyed shard', () => {
  const viaPage = readInternationalForCountryPage('ug', dir);
  assert.deepEqual(viaPage.map(item => item.event_id), ['who_don:ug-1']);
});

test('sub-national pages never inherit their parent country notices', () => {
  assert.ok(readInternationalByCountry('US', dir), 'fixture US shard exists');
  assert.equal(readInternationalForCountryPage('us', dir).length, 1);
  assert.deepEqual(readInternationalForCountryPage('us-ny', dir), []);
  assert.deepEqual(readInternationalForCountryPage('au-act', dir), []);
});

test('unknown slugs and traversal attempts resolve to nothing', () => {
  assert.deepEqual(readInternationalForCountryPage('zzz-nope', dir), []);
  assert.deepEqual(readInternationalForCountryPage('../../etc/passwd', dir), []);
  assert.deepEqual(readInternationalForCountryPage('', dir), []);
});

test('recentNotices accepts a raw array, caps it and keeps newest first', () => {
  const top = recentNotices(readInternationalForCountryPage('cd', dir), 2);
  assert.deepEqual(top.map(item => item.event_id), ['who_don:cd-2', 'who_don:cd-3']);
  assert.deepEqual(recentNotices(null), []);
});
