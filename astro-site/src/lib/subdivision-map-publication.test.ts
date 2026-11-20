import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mapDataForCountry } from './subdivision-map-publication.ts';
import { createSurveillanceDataClient } from './surveillance-data.ts';

function client(release_id: string, paths: string[] = []) {
  return createSurveillanceDataClient({
    fetcher: async () => Response.json({
      schema_version: 1, release_id, files: Object.fromEntries(paths.map(key =>
        [key, { url: key, sha256: 'a'.repeat(64) }])),
    }),
  });
}

test('only the affected publication and countries use the pinned Pages map files', async () => {
  const legacy = client('data-download-cells-20261119-44486d79');
  const next = client('new-surveillance-generation');
  const pages = client('pages-maps-sample', ['subdivision-maps/cn/index.json', 'subdivision-maps/us/index.json']);
  const signal = new AbortController().signal;
  for (const code of ['CN', 'US']) {
    assert.equal(await mapDataForCountry(code, signal, legacy, pages), pages);
    assert.equal(await mapDataForCountry(code, signal, next, pages), next);
    assert.equal(await mapDataForCountry(code, signal, legacy, client('empty')), legacy);
  }
  assert.equal(await mapDataForCountry('KR', signal, legacy, pages), legacy);
});
