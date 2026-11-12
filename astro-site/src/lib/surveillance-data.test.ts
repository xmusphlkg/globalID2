import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import { createSurveillanceDataClient } from './surveillance-data.ts';

const sha = (body: string | ArrayBuffer) => createHash('sha256').update(typeof body === 'string' ? body : new Uint8Array(body)).digest('hex');
const file = (body: string, url: string) => ({ url, sha256: sha(body), bytes: Buffer.byteLength(body) });
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });

test('one page pins summary, curves and lazy source details to one publication', async () => {
  const calls: string[] = [];
  const credentials: Array<RequestCredentials | undefined> = [];
  const bodies: Record<string, string> = { '/objects/a.json': '{"value":1}', '/objects/b.json': '{"value":2}' };
  let publication = { schema_version: 1, release_id: 'first', files: {
    'countries/ca-summary.json': file(bodies['/objects/a.json'], '/objects/a.json'),
    'countries/ca.json': file(bodies['/objects/b.json'], '/objects/b.json'),
  } };
  const client = createSurveillanceDataClient({ baseUrl: 'https://example.test/countries/ca/', manifestUrl: '/site-data/current.json',
    digest: async body => sha(body), fetcher: async (url: any, init?: RequestInit) => {
      const path = new URL(String(url), 'https://example.test').pathname; calls.push(path);
      credentials.push(init?.credentials);
      return path.endsWith('current.json') ? json(publication) : new Response(bodies[path]);
    } });
  const summary = await client.read('/site-data/countries/ca-summary.json');
  publication = { ...publication, release_id: 'second', files: {} as any };
  assert.deepEqual(await client.read('/site-data/countries/ca.json'), { value: 2 });
  assert.equal(await client.read('/site-data/countries/ca-summary.json'), summary);
  assert.equal((await client.manifest())?.release_id, 'first');
  assert.equal(calls.filter(path => path.endsWith('current.json')).length, 1);
  assert.deepEqual(credentials, ['same-origin', 'same-origin', 'same-origin']);
});

test('manifest 404 migrates through legacy paths; existing manifest never mixes missing or corrupt files', async () => {
  const legacyCalls: string[] = [];
  const legacy = createSurveillanceDataClient({ manifestUrl: '/site-data/current.json',
    fetcher: async (url: any) => { legacyCalls.push(String(url)); return String(url).endsWith('current.json') ? json({}, 404) : json({ legacy: true }); } });
  assert.deepEqual(await legacy.read('/site-data/countries/ca.json'), { legacy: true });
  assert.equal(legacyCalls.length, 2);
  const paths: string[] = [];
  const current = createSurveillanceDataClient({ manifestUrl: '/site-data/current.json', digest: async body => sha(body),
    fetcher: async (url: any) => { paths.push(String(url)); return String(url).endsWith('current.json')
      ? json({ schema_version: 1, release_id: 'new', files: { 'countries/ca.json': file('{"v":1}', 'objects/a.json') } })
      : new Response('{"v":2}'); } });
  await assert.rejects(current.read('/site-data/diseases/d038.json'), /absent from publication/);
  await assert.rejects(current.read('/site-data/countries/ca.json'), /checksum mismatch/);
  assert.equal(paths.some(path => path === '/site-data/diseases/d038.json'), false);
});

test('failed manifest requests can retry and invalid manifests or object sizes fail closed', async () => {
  let attempts = 0;
  const client = createSurveillanceDataClient({ manifestUrl: '/site-data/current.json',
    fetcher: async () => ++attempts === 1 ? json({}, 503) : json({ schema_version: 1, release_id: 'ok', files: {} }) });
  await assert.rejects(client.manifest(), /unavailable/);
  assert.equal((await client.manifest())?.release_id, 'ok');
  const invalid = createSurveillanceDataClient({ manifestUrl: '/site-data/current.json', fetcher: async () => json({ schema_version: 1, release_id: 'bad', files: { '../secret.json': file('{}', 'x.json') } }) });
  await assert.rejects(invalid.manifest(), /Invalid data publication file/);
  const sized = createSurveillanceDataClient({ manifestUrl: '/site-data/current.json', digest: async body => sha(body), fetcher: async (url: any) => String(url).endsWith('current.json')
    ? json({ schema_version: 1, release_id: 'bad', files: { 'meta.json': file('{}', 'objects/a.json') } }) : new Response('{"larger":true}') });
  await assert.rejects(sized.read('/site-data/meta.json'), /size mismatch/);
});

test('large curve readers can release raw JSON while keeping the publication pinned', async () => {
  let manifests = 0; let objects = 0; const body = '{"points":[1,2,3]}';
  const client = createSurveillanceDataClient({ manifestUrl: '/site-data/current.json', digest: async value => sha(value), fetcher: async (url: any) => {
    if (String(url).endsWith('current.json')) { manifests++; return json({ schema_version: 1, release_id: 'one', files: { 'countries/jp.json': file(body, 'objects/one.json') } }); }
    objects++; return new Response(body);
  } });
  const [a, b] = await Promise.all([client.read('/site-data/countries/jp.json', { retain: false }), client.read('/site-data/countries/jp.json', { retain: false })]);
  assert.equal(a, b); assert.equal(objects, 1);
  assert.deepEqual(await client.read('/site-data/countries/jp.json', { retain: false }), a);
  assert.equal(objects, 2); assert.equal(manifests, 1);
});

test('HTTP gzip representations verify decoded JSON bytes rather than Content-Length', async () => {
  const body = '{"generated_at":"2026-11-07"}';
  const client = createSurveillanceDataClient({ manifestUrl: '/site-data/current.json', digest: async value => sha(value), fetcher: async (url: any) => String(url).endsWith('current.json')
    ? json({ schema_version: 1, release_id: 'compressed', files: { 'site-status.json': file(body, 'objects/status.json') } })
    // Fetch has already decompressed this response before arrayBuffer(). The
    // Content-Length still describes the compressed HTTP representation.
    : new Response(body, { headers: { 'content-encoding': 'gzip', 'content-length': '999' } }) });
  assert.deepEqual(await client.read('/site-data/site-status.json'), { generated_at: '2026-11-07' });
});

test('independent Situation namespace pins its map and late outbreak without using surveillance', async () => {
  const bodies: Record<string, string> = { '/site-data/situation/objects/map.json': '{"points":[1]}', '/site-data/situation/objects/outbreak.json': '{"alerts":[1]}' };
  let release = 'first'; const calls: string[] = [];
  const client = createSurveillanceDataClient({ baseUrl: 'https://example.test/situation/', logicalRoot: '/situation/', manifestUrl: '/site-data/situation/current.json', digest: async value => sha(value), fetcher: async (url: any) => {
    const path = new URL(String(url)).pathname; calls.push(path);
    return path.endsWith('current.json') ? json({ schema_version: 1, release_id: release, files: {
      'map-data.json': file(bodies['/site-data/situation/objects/map.json'], 'objects/map.json'),
      'outbreaks/test.json': file(bodies['/site-data/situation/objects/outbreak.json'], 'objects/outbreak.json'),
    } }) : new Response(bodies[path]);
  } });
  assert.deepEqual(await client.read('/situation/map-data.json'), { points: [1] });
  release = 'second';
  assert.deepEqual(await client.read('/situation/outbreaks/test.json'), { alerts: [1] });
  assert.equal((await client.manifest())?.release_id, 'first');
  assert.equal(calls.filter(path => path.endsWith('current.json')).length, 1);
  assert.ok(calls.every(path => path.startsWith('/site-data/situation/')));
});

test('small bootstrap renders a summary before loading the complete pinned manifest', async () => {
  const summary = '{"total":43}', detail = '{"points":[1,2]}';
  const full = JSON.stringify({schema_version:1,release_id:'first',files:{'home-summary.json':file(summary,'objects/summary.json'),'countries/jp.json':file(detail,'objects/detail.json')}});
  const calls: string[] = [];
  let release = 'first';
  const client = createSurveillanceDataClient({baseUrl:'https://example.test/',digest:async body=>sha(body),fetcher:async (url:any)=>{
    const path=new URL(String(url)).pathname;calls.push(path);
    if(path.endsWith('bootstrap.json'))return json({schema_version:1,release_id:release,files:{'home-summary.json':file(summary,'objects/summary.json')},manifest:file(full,'manifests/first.json')});
    return new Response(path.endsWith('manifests/first.json')?full:path.endsWith('summary.json')?summary:detail);
  }});
  assert.deepEqual(await client.read('/site-data/home-summary.json'),{total:43});
  assert.deepEqual(calls,['/site-data/bootstrap.json','/site-data/objects/summary.json']);
  release='second';
  const [a,b]=await Promise.all([client.read('/site-data/countries/jp.json'),client.read('/site-data/countries/jp.json')]);
  assert.deepEqual(a,{points:[1,2]});assert.equal(a,b);
  assert.equal((await client.manifest())?.release_id,'first');
  assert.equal(calls.filter(path=>path.includes('/manifests/')).length,1);
  assert.equal(calls.some(path=>path.endsWith('current.json')),false);
});

test('a missing bootstrap supports legacy servers; corrupt lazy manifests never fall back to current data', async () => {
  const calls:string[]=[];
  const legacy=createSurveillanceDataClient({fetcher:async(url:any)=>{calls.push(String(url));return String(url).endsWith('bootstrap.json')?json({},404):json({schema_version:1,release_id:'legacy',files:{}});}});
  assert.equal((await legacy.manifest())?.release_id,'legacy');assert.equal(calls.length,2);
  const body=JSON.stringify({schema_version:1,release_id:'one',files:{}});
  const corrupt=createSurveillanceDataClient({digest:async value=>sha(value),fetcher:async(url:any)=>String(url).endsWith('bootstrap.json')?json({schema_version:1,release_id:'one',files:{},manifest:file(body,'manifests/one.json')}):new Response(body+' ')});
  await assert.rejects(corrupt.read('/site-data/countries/jp.json'),/publication checksum mismatch/);
});
