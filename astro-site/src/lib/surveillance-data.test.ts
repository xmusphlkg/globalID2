import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import { createSurveillanceDataClient } from './surveillance-data.ts';

const sha = (body: string | ArrayBuffer) => createHash('sha256').update(typeof body === 'string' ? body : new Uint8Array(body)).digest('hex');
const file = (body: string, url: string) => ({ url, sha256: sha(body), bytes: Buffer.byteLength(body) });
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const deferred = <T>() => {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};

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

for (const phase of ['bootstrap.json', 'current.json', 'manifests/one.json', 'objects/one.json']) {
  for (const stalledPart of ['fetch', 'body']) {
    test(`${phase} has a bounded ${stalledPart} wait and can retry`, async context => {
      context.mock.timers.enable({ apis: ['setTimeout'] });
      const body = '{"value":1}';
      const full = { schema_version: 1, release_id: 'one', files: { 'countries/jp.json': file(body, 'objects/one.json') } };
      const fullBody = JSON.stringify(full);
      const bootstrap = { ...full, files: {}, manifest: file(fullBody, 'manifests/one.json') };
      const blocked = deferred<void>();
      let stalled = true;
      let blockedSignal: AbortSignal | undefined;
      let attempts = 0;
      const client = createSurveillanceDataClient({ requestTimeoutMs: 100, digest: async value => sha(value), fetcher: async (url: any, init?: RequestInit) => {
        const path = new URL(String(url)).pathname;
        if (path.endsWith(phase)) {
          attempts++;
          if (stalled) {
            blockedSignal = init!.signal!;
            if (stalledPart === 'fetch') { blocked.resolve(); return new Promise<Response>(() => {}); }
            const response = new Response();
            const hang = () => { blocked.resolve(); return new Promise<never>(() => {}); };
            response.json = hang;
            response.arrayBuffer = hang;
            return response;
          }
        }
        if (path.endsWith('bootstrap.json')) return phase === 'current.json' ? json({}, 404) : json(bootstrap);
        if (path.endsWith('current.json')) return json(full);
        return new Response(path.endsWith('manifests/one.json') ? fullBody : body);
      } });
      const failed = assert.rejects(client.read('/site-data/countries/jp.json'), { name: 'TimeoutError' });
      await blocked.promise;
      context.mock.timers.tick(99);
      assert.equal(blockedSignal?.aborted, false);
      context.mock.timers.tick(1);
      await failed;
      assert.equal(blockedSignal?.aborted, true);
      stalled = false;
      assert.deepEqual(await client.read('/site-data/countries/jp.json'), { value: 1 });
      assert.equal(attempts, 2);
    });
  }
}

for (const path of ['/other/dataset.json', '/site-data/legacy.json']) {
  test(`body waits on ${path} also time out`, async context => {
    context.mock.timers.enable({ apis: ['setTimeout'] });
    const blocked = deferred<void>();
    let signal: AbortSignal | undefined;
    const client = createSurveillanceDataClient({ requestTimeoutMs: 100, fetcher: async (url: any, init?: RequestInit) => {
      if (String(url).endsWith('bootstrap.json') || String(url).endsWith('current.json')) return json({}, 404);
      signal = init!.signal!;
      const response = new Response();
      response.json = () => { blocked.resolve(); return new Promise<never>(() => {}); };
      return response;
    } });
    const failed = assert.rejects(client.read(path), { name: 'TimeoutError' });
    await blocked.promise;
    context.mock.timers.tick(100);
    await failed;
    assert.equal(signal?.aborted, true);
  });
}

for (const phase of ['bootstrap.json', 'current.json', 'manifests/one.json', 'objects/one.json']) {
  test(`canceling a consumer during ${phase} leaves other consumers and the in-flight cache intact`, async () => {
    const body = '{"value":1}';
    const full = JSON.stringify({ schema_version: 1, release_id: 'one', files: { 'a.json': file(body, 'objects/one.json'), 'b.json': file(body, 'objects/one.json') } });
    const bootstrap = { schema_version: 1, release_id: 'one', files: {}, manifest: file(full, 'manifests/one.json') };
    const blocked = deferred<void>();
    const resume = deferred<void>();
    const calls: string[] = [];
    let blockedSignal: AbortSignal | undefined;
    const client = createSurveillanceDataClient({ digest: async value => sha(value), fetcher: async (url: any, init?: RequestInit) => {
      const path = new URL(String(url)).pathname;
      calls.push(path);
      if (path.endsWith(phase)) { blockedSignal = init!.signal!; blocked.resolve(); await resume.promise; }
      if (path.endsWith('bootstrap.json')) return phase === 'current.json' ? json({}, 404) : json(bootstrap);
      return path.endsWith('current.json') ? json(bootstrap) : new Response(path.endsWith('manifests/one.json') ? full : body);
    } });
    const controller = new AbortController();
    const canceled = assert.rejects(client.read('/site-data/a.json', { signal: controller.signal, retain: false }), { name: 'AbortError' });
    // Different keys must share the lazy manifest as well as bootstrap.
    const other = client.read(phase.startsWith('manifests/') ? '/site-data/b.json' : '/site-data/a.json', { retain: false });
    await blocked.promise;
    controller.abort();
    await canceled;
    assert.equal(blockedSignal?.aborted, false);
    const replacement = client.read('/site-data/a.json', { retain: false });
    resume.resolve();
    assert.deepEqual(await other, { value: 1 });
    assert.deepEqual(await replacement, { value: 1 });
    const objectCalls = phase.startsWith('manifests/') ? 2 : 1;
    assert.equal(calls.filter(path => path.endsWith('objects/one.json')).length, objectCalls);
    assert.equal(calls.filter(path => path.endsWith('bootstrap.json')).length, 1);
    assert.equal(calls.filter(path => path.endsWith('manifests/one.json')).length, 1);
    await client.read('/site-data/a.json', { retain: false });
    assert.equal(calls.filter(path => path.endsWith('objects/one.json')).length, objectCalls + 1);
  });
}

test('successful retain:true consumers keep the cache even when retain:false consumers finish later', async () => {
  const body = '{"value":1}';
  let objects = 0;
  const client = createSurveillanceDataClient({ digest: async value => sha(value), fetcher: async (url: any) => {
    if (String(url).endsWith('bootstrap.json')) return json({ schema_version: 1, release_id: 'one', files: { 'a.json': file(body, 'objects/one.json') } });
    objects++;
    return new Response(body);
  } });
  const [retained, released] = await Promise.all([client.read('/site-data/a.json'), client.read('/site-data/a.json', { retain: false })]);
  assert.equal(retained, released);
  assert.equal(await client.read('/site-data/a.json', { retain: false }), retained);
  assert.equal(await client.read('/site-data/a.json'), retained);
  assert.equal(objects, 1);
});

test('all canceled consumers release unretained results while preserving the pinned manifest', async () => {
  const body = '{"value":1}';
  const blocked = deferred<void>();
  const resume = deferred<void>();
  let objects = 0;
  let manifests = 0;
  const client = createSurveillanceDataClient({ digest: async value => sha(value), fetcher: async (url: any) => {
    if (String(url).endsWith('bootstrap.json')) { manifests++; return json({ schema_version: 1, release_id: 'one', files: { 'a.json': file(body, 'objects/one.json') } }); }
    objects++;
    blocked.resolve();
    await resume.promise;
    return new Response(body);
  } });
  const a = new AbortController();
  const b = new AbortController();
  const canceledA = assert.rejects(client.read('/site-data/a.json', { signal: a.signal }), { name: 'AbortError' });
  const canceledB = assert.rejects(client.read('/site-data/a.json', { signal: b.signal, retain: false }), { name: 'AbortError' });
  await blocked.promise;
  a.abort(); b.abort();
  await Promise.all([canceledA, canceledB]);
  resume.resolve();
  // Drain completion, including response decoding and the abandoned consumers'
  // handlers. Timers are not involved in this successful-request cleanup.
  await new Promise<void>(resolve => setImmediate(resolve));
  assert.deepEqual(await client.read('/site-data/a.json'), { value: 1 });
  assert.equal(objects, 2);
  assert.equal(manifests, 1);
});

test('abandoned requests abort at their deadline, and a late failure cannot evict the retry', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  const body = '{"value":1}';
  const blocked = deferred<void>();
  const firstObject = deferred<Response>();
  const retryStarted = deferred<void>();
  const retryObject = deferred<Response>();
  let objects = 0;
  let objectSignal: AbortSignal | undefined;
  const client = createSurveillanceDataClient({ requestTimeoutMs: 100, digest: async value => sha(value), fetcher: async (url: any, init?: RequestInit) => {
    if (String(url).endsWith('bootstrap.json')) return json({ schema_version: 1, release_id: 'one', files: { 'a.json': file(body, 'objects/one.json') } });
    if (++objects === 1) { objectSignal = init!.signal!; blocked.resolve(); return firstObject.promise; }
    retryStarted.resolve();
    return retryObject.promise;
  } });
  const controller = new AbortController();
  const canceled = assert.rejects(client.read('/site-data/a.json', { signal: controller.signal, retain: false }), { name: 'AbortError' });
  await blocked.promise;
  controller.abort();
  await canceled;
  context.mock.timers.tick(100);
  await new Promise<void>(resolve => setImmediate(resolve));
  assert.equal(objectSignal?.aborted, true);
  const retried = client.read('/site-data/a.json', { retain: false });
  await retryStarted.promise;
  firstObject.reject(new Error('late original failure'));
  await new Promise<void>(resolve => setImmediate(resolve));
  const sharedRetry = client.read('/site-data/a.json', { retain: false });
  retryObject.resolve(new Response(body));
  assert.equal(await retried, await sharedRetry);
  assert.equal(objects, 2);
});

test('already canceled callers start no requests, and manifest subscribers cancel independently', async () => {
  const resume = deferred<Response>();
  let calls = 0;
  const client = createSurveillanceDataClient({ fetcher: async () => { calls++; return resume.promise; } });
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(client.read('/site-data/a.json', { signal: controller.signal }), { name: 'AbortError' });
  await assert.rejects(client.manifest({ signal: controller.signal }), { name: 'AbortError' });
  assert.equal(calls, 0);
  const other = new AbortController();
  const canceled = assert.rejects(client.manifest({ signal: other.signal }), { name: 'AbortError' });
  const waiting = client.manifest();
  other.abort();
  await canceled;
  resume.resolve(json({ schema_version: 1, release_id: 'one', files: {} }));
  assert.equal((await waiting)?.release_id, 'one');
  assert.equal(calls, 1);
});

test('successful requests clear their deadlines after completing the response body', async context => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  const signals: AbortSignal[] = [];
  const body = '{"value":1}';
  const client = createSurveillanceDataClient({ requestTimeoutMs: 100, digest: async value => sha(value), fetcher: async (url: any, init?: RequestInit) => {
    signals.push(init!.signal!);
    return String(url).endsWith('bootstrap.json')
      ? json({ schema_version: 1, release_id: 'one', files: { 'a.json': file(body, 'objects/one.json') } })
      : new Response(body);
  } });
  assert.deepEqual(await client.read('/site-data/a.json'), { value: 1 });
  context.mock.timers.tick(100);
  assert.equal(signals.length, 2);
  assert.ok(signals.every(signal => !signal.aborted));
});
