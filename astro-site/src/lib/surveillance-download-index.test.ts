import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';
import { createSurveillanceDataClient } from './surveillance-data.ts';
import { readSurveillanceDownloadIndex } from './surveillance-download-index.ts';
import { downloadCatalogue } from '../components/public/downloads/catalogue.ts';

const country = (code = 'JP', version = 1) => ({ kind: 'country', id: code.toLowerCase(), code,
  name_en: code === 'JP' ? 'Japan' : 'Tokyo', name_zh: code === 'JP' ? '日本' : '东京都',
  generated_at: `2026-10-0${version}`, record_count: 100 * version, parts_count: 10,
  parts: [{ id: `202${version}`, label: `202${version}`, record_count: 20 * version,
    files: { csv: { url: `https://files.example.test/${code}-v${version}.csv`, bytes: 1000 } } }] });
const index = (version = 1) => ({ schema_version: 1, generated_at: `2026-10-0${version}`, surveillance_release_id: 'independent-download-release',
  countries: [country('JP', version), country('JP-13', version)], diseases: [] });
const meta = { countries: [{ code: 'JP', location_type: 'country' }, { code: 'JP-13', location_type: 'subdivision', parent_code: 'JP' }] };
const sha = (body: string | ArrayBuffer) => createHash('sha256').update(typeof body === 'string' ? body : new Uint8Array(body)).digest('hex');
function server() {
  const calls: string[] = [], bodies = new Map<string, string>();
  let current: any;
  const publish = (directory: any = index(), metadata: any = meta, release = 'release-1') => {
    const files = Object.fromEntries(Object.entries({ 'downloads/index.json': directory, 'meta.json': metadata }).map(([key, value]) => {
      const body = JSON.stringify(value), hash = sha(body), url = `/site-data/objects/${hash}.json`;
      bodies.set(url, body);
      return [key, { url, sha256: hash, bytes: Buffer.byteLength(body) }];
    }));
    current = { schema_version: 1, release_id: release, files };
    return current;
  };
  publish();
  const client = () => createSurveillanceDataClient({ manifestUrl: '/site-data/current.json', baseUrl: 'https://example.test/',
    digest: async body => sha(body), fetcher: async url => {
      const path = new URL(String(url), 'https://example.test').pathname; calls.push(path);
      return path.endsWith('/current.json') ? new Response(JSON.stringify(current))
        : new Response(bodies.get(path), { status: bodies.has(path) ? 200 : 404 });
    } });
  return { publish, client, calls, bodies, current: () => current };
}

test('compact directory pins index and meta, preserves true period counts, and needs only two datasets', async () => {
  const state = server(), client = state.client();
  await client.manifest();
  state.publish(index(2), meta, 'release-2');
  const first = (await readSurveillanceDownloadIndex({ client }))!;
  assert.equal(first.version, 'release-1');
  assert.equal(first.index.surveillance_release_id, 'independent-download-release');
  const model = downloadCatalogue(first.index, first.meta, 'zh');
  assert.equal(model.countries.length, 1);
  assert.equal(model.subdivisionCount, 1);
  assert.match(model.entries[0].detail, /当前文件 20 条.*全部历史 100 条（10 个时间范围）/);
  assert.equal(model.entries[0].files.csv.url, 'https://files.example.test/JP-v1.csv');
  assert.equal(state.calls.length, 3);
  assert.ok(state.calls.slice(1).every(path => path.includes('/objects/')));
  const next = (await readSurveillanceDownloadIndex({ client: state.client() }))!;
  assert.equal(next.version, 'release-2');
  assert.equal(next.index.countries[0].record_count, 200);
});

test('only a completely absent manifest permits a static directory', async () => {
  const client = createSurveillanceDataClient({ fetcher: async () => new Response('', { status: 404 }) });
  assert.equal(await readSurveillanceDownloadIndex({ client }), null);
});

test('a published directory never falls back on missing, corrupt or oversized-history index entries', async () => {
  for (const mode of ['missing-key', 'corrupt', 'missing-object', 'history', 'unsafe-url', 'duplicate', 'bad-meta', 'bad-schema']) {
    const state = server(), value = index();
    if (mode === 'history') value.countries[0].parts.push(value.countries[0].parts[0]);
    if (mode === 'unsafe-url') value.countries[0].parts[0].files.csv.url = 'javascript:alert(1)';
    if (mode === 'duplicate') value.countries.push(value.countries[0]);
    if (mode === 'bad-schema') value.schema_version = 4;
    const manifest = state.publish(value, mode === 'bad-meta' ? {} : meta);
    const url = manifest.files['downloads/index.json'].url;
    if (mode === 'missing-key') delete manifest.files['downloads/index.json'];
    if (mode === 'missing-object') state.bodies.delete(url);
    if (mode === 'corrupt') state.bodies.set(url, '{}');
    await assert.rejects(readSurveillanceDownloadIndex({ client: state.client() }), /Invalid|mismatch|absent|unavailable/);
    assert.ok(state.calls.every(path => !path.endsWith('/downloads/index.json') && !path.endsWith('-summary.json')));
  }
});
