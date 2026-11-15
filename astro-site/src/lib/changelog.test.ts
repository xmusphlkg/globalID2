import assert from 'node:assert/strict';
import test from 'node:test';
import { changelogReleases } from '../data/changelog.ts';
import { readChangelog } from './changelog.ts';

test('the complete multilingual catalogue can be published independently of templates', () => {
  assert.deepEqual(readChangelog(JSON.parse(JSON.stringify({ schema_version: 1, releases: changelogReleases }))), changelogReleases);
});

test('rejects invalid feed versions, duplicate releases, impossible dates and missing translations', () => {
  const release = changelogReleases[0];
  for (const value of [null, { schema_version: 2, releases: [release] }, { schema_version: 1, releases: [] },
    { schema_version: 1, releases: [release, release] },
    ...[{ date: '2026-02-30' }, { version: '<script>' }, { titleFr: '' }, { sections: [] },
      { sections: [{ ...release.sections[0], items: [{ en: 'English only' }] }] }]
      .map(change => ({ schema_version: 1, releases: [{ ...release, ...change }] })),
  ]) assert.throws(() => readChangelog(value));
});
