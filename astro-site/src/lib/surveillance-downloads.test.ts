import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';
import { createSurveillanceDataClient } from './surveillance-data.ts';
import { readSurveillanceDownloads } from './surveillance-downloads.ts';

// Native schema-4 download manifest entries do not contain schema_version.
const entry = (kind = 'countries', id = 'JP', version = 1) => ({
  kind: kind === 'countries' ? 'country' : 'disease', id: id.toLowerCase(),
  ...(kind === 'countries' ? { code: id } : { disease_id: id }),
  generated_at: `2026-10-0${version}T00:00:00+00:00`, record_count: 12,
  source_observation_count: 12, includes_series_provenance: true,
  date_range: { start: '2025-01-01', end: '2025-12-31' },
  parts: [{ id: '2025', is_current: true, label: '2025', record_count: 12,
    date_range: { start: '2025-01-01', end: '2025-12-31' },
    files: Object.fromEntries(['csv', 'json', 'xlsx'].map(format => [format, {
      url: `https://files.example.test/v${version}/${id.toLowerCase()}.${format}`,
      filename: `${id.toLowerCase()}-${version}.${format}`, bytes: 1024,
    }])) }],
});
const sha = (body: string | ArrayBuffer) => createHash('sha256').update(typeof body === 'string' ? body : new Uint8Array(body)).digest('hex');
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
function server(kind = 'countries', id = 'JP', download: unknown = entry(kind, id)) {
  const key = `${kind}/${id.toLowerCase()}-summary.json`, calls: string[] = [];
  const summary = { schema_version: 1, [kind === 'countries' ? 'country_code' : 'disease_id']: id,
    generated_at: '2026-11-16', download };
  const bodies = new Map<string, string>();
  let current: any;
  const publish = (value: any, version = 'first') => {
    const body = JSON.stringify(value), hash = sha(body), url = `/site-data/objects/${hash}.json`;
    bodies.set(url, body);
    current = { schema_version: 1, release_id: version, files: { [key]: { url, sha256: hash, bytes: Buffer.byteLength(body) } } };
    return url;
  };
  const object = publish(summary);
  const fetcher: typeof fetch = async url => {
    const path = new URL(String(url), 'https://example.test').pathname;
    calls.push(path);
    return path.endsWith('current.json') ? json(current) : new Response(bodies.get(path), { status: bodies.has(path) ? 200 : 404 });
  };
  const client = () => createSurveillanceDataClient({ manifestUrl: '/site-data/current.json', baseUrl: 'https://example.test/', fetcher, digest: async body => sha(body) });
  return { key, summary, publish, fetcher, client, calls, bodies, object };
}
const noFallback: typeof fetch = async () => { throw new Error('legacy fallback must not be used'); };

for (const [kind, id] of [['countries', 'JP'], ['diseases', 'D038']]) {
  test(`native ${kind} entry preserves download date and stays pinned until the next page`, async () => {
    const state = server(kind, id), client = state.client(), path = `/site-data/${state.key}`;
    await client.read(path); // Summary rendered by another island first.
    state.publish({ ...state.summary, download: entry(kind, id, 2) }, 'second');
    const first = await readSurveillanceDownloads(path, { client, fetcher: noFallback });
    assert.equal(first.version, 'first');
    assert.equal(first.data.generated_at, '2026-10-01T00:00:00+00:00');
    assert.equal(first.data.parts[0].files.csv.filename, `${id.toLowerCase()}-1.csv`);
    assert.equal(first.data.source_observation_count, 12);
    assert.equal(state.calls.filter(path => path.includes('/objects/')).length, 1);
    const next = await readSurveillanceDownloads(path, { client: state.client(), fetcher: noFallback });
    assert.equal(next.version, 'second');
    assert.equal(next.data.parts[0].files.csv.filename, `${id.toLowerCase()}-2.csv`);
  });
}

test('a deployment without either manifest can use the legacy catalogue', async () => {
  const paths: string[] = [];
  const client = createSurveillanceDataClient({ manifestUrl: '/site-data/bootstrap.json', baseUrl: 'https://example.test/',
    fetcher: async url => { paths.push(String(url)); return json({}, 404); } });
  const legacy = { ...entry(), schema_version: 1 };
  const result = await readSurveillanceDownloads('/site-data/countries/jp-summary.json', { client, fetcher: async url => {
    assert.ok(String(url).endsWith('/site-data/downloads/countries/jp.json')); return json(legacy);
  } });
  assert.equal(result.source, 'legacy');
  assert.equal(result.version, undefined);
  assert.equal(paths.length, 2);
});

test('only an older summary with no download property permits a static fallback', async () => {
  const state = server();
  const { download: _, ...oldSummary } = state.summary;
  state.publish(oldSummary);
  let reads = 0;
  const result = await readSurveillanceDownloads(`/site-data/${state.key}`, { client: state.client(), fetcher: async () => {
    reads++; return json({ ...entry(), schema_version: 1 });
  } });
  assert.equal(reads, 1);
  assert.equal(result.source, 'legacy');
  assert.equal(result.version, undefined);
});

test('explicit null and malformed or cross-scope metadata never use a static fallback', async () => {
  const wrongFile = entry(); wrongFile.parts[0].files.csv.url = 'javascript:alert(1)';
  const duplicate = entry(); duplicate.parts.push(duplicate.parts[0]);
  for (const download of [null, {}, { ...entry(), code: 'US' }, { ...entry(), kind: 'disease' },
    { ...entry(), generated_at: 'invalid' }, { ...entry(), record_count: -1 }, wrongFile, duplicate]) {
    const state = server('countries', 'JP', download);
    await assert.rejects(readSurveillanceDownloads(`/site-data/${state.key}`, { client: state.client(), fetcher: noFallback }),
      /Invalid|mismatch/);
  }
});

test('checksum errors, absent objects and manifest failures cannot downgrade to legacy', async () => {
  for (const mode of ['corrupt', 'missing', 'manifest-error']) {
    const state = server();
    if (mode === 'corrupt') state.bodies.set(state.object, '{"tampered":true}');
    if (mode === 'missing') state.bodies.delete(state.object);
    const client = mode === 'manifest-error' ? createSurveillanceDataClient({ fetcher: async () => json({}, 503) }) : state.client();
    await assert.rejects(readSurveillanceDownloads(`/site-data/${state.key}`, { client, fetcher: noFallback }),
      /mismatch|unavailable/);
  }
});
