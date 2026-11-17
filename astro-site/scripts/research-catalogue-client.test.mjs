import assert from 'node:assert/strict';
import { test } from 'node:test';
import { loadResearchCatalogue } from '../src/lib/research-catalogue-client.ts';

test('loads only requested catalogue chunks and narrows collection requests', async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  const fixtures = {
    '/research/catalogue/manifest.json': { chunks: [
      { path: '/research/catalogue/first.json', count: 2, facets: { disease: ['d001'], country: [], topic: [], week: [] } },
      { path: '/research/catalogue/second.json', count: 1, facets: { disease: ['d002'], country: [], topic: [], week: [] } },
    ] },
    '/research/catalogue/first.json': { articles: [{ slug: 'one' }, { slug: 'two' }] },
    '/research/catalogue/second.json': { articles: [{ slug: 'three' }] },
  };
  globalThis.fetch = async path => {
    requests.push(path);
    return { ok: true, json: async () => fixtures[path] };
  };
  try {
    assert.deepEqual((await loadResearchCatalogue({ limit: 1 })).map(item => item.slug), ['one', 'two']);
    assert.deepEqual(requests, ['/research/catalogue/manifest.json', '/research/catalogue/first.json']);
    assert.deepEqual((await loadResearchCatalogue({ scope: 'disease', value: 'd002' })).map(item => item.slug), ['three']);
    assert.equal(requests.filter(path => path === '/research/catalogue/first.json').length, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('focused Ask uses catalogue facets and exact membership', async () => {
  const { loadResearchQuestionCatalogue } = await import('../src/lib/research-catalogue-client.ts?scoped');
  const originalFetch = globalThis.fetch, requests = [];
  const fixtures = {
    '/research/catalogue/entities.json': { entities: [{ scope: 'disease', value: 'd038', aliases: ['flu'] }] },
    '/research/catalogue/manifest.json': { chunks: [
      { path: '/matching.json', count: 2, facets: { disease: ['d038'] } },
      { path: '/other.json', count: 1, facets: { disease: ['d999'] } },
    ] },
    '/matching.json': { articles: [
      { article_id: 'one', diseases: [{ disease_id: 'D038' }] },
      { article_id: 'other', diseases: [{ disease_id: 'D999' }] },
    ] },
  };
  globalThis.fetch = async path => { requests.push(path); return { ok: true, json: async () => fixtures[path] }; };
  try {
    assert.deepEqual((await loadResearchQuestionCatalogue('flu')).map(item => item.article_id), ['one']);
    assert.deepEqual(requests, ['/research/catalogue/entities.json', '/research/catalogue/manifest.json', '/matching.json']);
  } finally { globalThis.fetch = originalFetch; }
});

test('generic Ask scans the complete catalogue with four requests at a time, monotonic progress and shared concurrent work', async () => {
  const { loadResearchQuestionCatalogue } = await import('../src/lib/research-catalogue-client.ts?generic-progress');
  const originalFetch = globalThis.fetch;
  const chunks = Array.from({ length: 11 }, (_, index) => ({ path: `/generic-${index}.json`, count: 1, facets: {} }));
  const requests = [], firstProgress = [], secondProgress = [];
  let active = 0, maximum = 0;
  globalThis.fetch = async path => {
    if (path === '/research/catalogue/entities.json') return { ok: true, json: async () => ({ entities: [] }) };
    if (path === '/research/catalogue/manifest.json') return { ok: true, json: async () => ({ chunks }) };
    requests.push(path); maximum = Math.max(maximum, ++active);
    const index = chunks.findIndex(chunk => chunk.path === path);
    await new Promise(resolve => setTimeout(resolve, (11 - index) % 4 + 2));
    active--;
    return { ok: true, json: async () => ({ articles: [{ article_id: String(index) }] }) };
  };
  try {
    const [first, second] = await Promise.all([
      loadResearchQuestionCatalogue('occupational exposure', { onProgress: event => firstProgress.push(event) }),
      loadResearchQuestionCatalogue('social determinants', { onProgress: event => secondProgress.push(event) }),
    ]);
    assert.equal(maximum, 4);
    assert.equal(requests.length, 11, 'overlapping searches share in-flight chunks');
    assert.deepEqual(first.map(article => article.article_id), chunks.map((_, index) => String(index)));
    assert.deepEqual(second, first);
    for (const progress of [firstProgress, secondProgress]) {
      assert.deepEqual(progress.map(event => event.completed), Array.from({ length: 12 }, (_, index) => index));
      assert.ok(progress.every(event => event.total === 11 && event.mode === 'catalogue'));
    }
  } finally { globalThis.fetch = originalFetch; }
});

test('multiple safe facet prefilters combine without losing legacy or unknown-metadata chunks', async () => {
  const { loadResearchCatalogue } = await import('../src/lib/research-catalogue-client.ts?multi-facet');
  const originalFetch = globalThis.fetch, requests = [], progress = [];
  const full = { disease: ['d038'], country: ['jp'], topic: ['vaccination'], study: ['mathematical modelling'] };
  const chunks = [
    { path: '/all-match.json', count: 2, facets: full },
    { path: '/other-disease.json', count: 1, facets: { ...full, disease: ['d029'] } },
    { path: '/other-study.json', count: 1, facets: { ...full, study: ['cohort study'] } },
    { path: '/old-study-unknown.json', count: 1, facets: { disease: ['d038'], country: ['jp'], topic: ['vaccination'] } },
    { path: '/old-all-unknown.json', count: 1 },
    { path: '/known-empty-study.json', count: 1, facets: { ...full, study: [] } },
    { path: '/malformed-study.json', count: 1, facets: { ...full, study: null } },
  ];
  globalThis.fetch = async path => {
    requests.push(path);
    if (path === '/research/catalogue/manifest.json') return { ok: true, json: async () => ({ chunks }) };
    return { ok: true, json: async () => ({ articles: path === '/all-match.json' ? [{ article_id: path }, { article_id: 'bibliographic', detail_available: false }] : [{ article_id: path }] }) };
  };
  try {
    const options = { facets: { disease: ' D038 ', country: 'JP', topic: 'vaccination', study: 'Mathematical modelling' }, onProgress: event => progress.push(event) };
    const records = await loadResearchCatalogue(options);
    assert.deepEqual(records.map(row => row.article_id), ['/all-match.json', '/old-study-unknown.json', '/old-all-unknown.json', '/malformed-study.json']);
    assert.deepEqual(requests.slice(1).sort(), ['/all-match.json', '/old-study-unknown.json', '/old-all-unknown.json', '/malformed-study.json'].sort());
    assert.deepEqual(progress.map(event => event.completed), [0, 1, 2, 3, 4]);
    assert.ok(progress.every(event => event.total === 4));
    const withBibliography = await loadResearchCatalogue({ ...options, includeBibliographic: true });
    assert.equal(withBibliography.length, 5);
    assert.equal(requests.length, 5, 'same candidate pages are cached');
    // The old scope API also keeps chunks whose old metadata cannot exclude it.
    const compatible = await loadResearchCatalogue({ scope: 'study', value: 'MATHEMATICAL MODELLING', facets: { country: 'JP' } });
    assert.ok(compatible.some(row => row.article_id === '/old-all-unknown.json'));
    assert.equal(compatible.some(row => row.article_id === '/other-study.json'), false);
  } finally { globalThis.fetch = originalFetch; }
});

test('different overlapping facet requests share a global four-request cap and complete all selected pages', async () => {
  const { loadResearchCatalogue } = await import('../src/lib/research-catalogue-client.ts?facet-concurrency');
  const originalFetch = globalThis.fetch;
  const chunks = Array.from({ length: 12 }, (_, index) => ({ path: `/faceted-${index}.json`, count: 1, facets: { study: [index < 6 ? 'mathematical modelling' : 'cohort study'] } }));
  let active = 0, maximum = 0;
  const requests = [];
  globalThis.fetch = async path => {
    if (path === '/research/catalogue/manifest.json') return { ok: true, json: async () => ({ chunks }) };
    requests.push(path); maximum = Math.max(maximum, ++active);
    await new Promise(resolve => setTimeout(resolve, 3)); active--;
    return { ok: true, json: async () => ({ articles: [{ article_id: path }] }) };
  };
  try {
    const [model, cohort, modelAgain] = await Promise.all([
      loadResearchCatalogue({ facets: { study: 'mathematical modelling' } }),
      loadResearchCatalogue({ facets: { study: 'cohort study' } }),
      loadResearchCatalogue({ facets: { study: 'mathematical modelling' } }),
    ]);
    assert.equal(maximum, 4);
    assert.equal(requests.length, 12);
    assert.deepEqual(model.map(row => row.article_id), chunks.slice(0, 6).map(chunk => chunk.path));
    assert.deepEqual(cohort.map(row => row.article_id), chunks.slice(6).map(chunk => chunk.path));
    assert.deepEqual(modelAgain, model);
  } finally { globalThis.fetch = originalFetch; }
});

test('failed filtered chunks can be retried without poisoning the global request queue', async () => {
  const { loadResearchCatalogue } = await import('../src/lib/research-catalogue-client.ts?facet-retry');
  const originalFetch = globalThis.fetch;
  let attempts = 0;
  globalThis.fetch = async path => path === '/research/catalogue/manifest.json'
    ? { ok: true, json: async () => ({ chunks: [{ path: '/retry-model.json', count: 1, facets: { study: ['mathematical modelling'] } }] }) }
    : (++attempts === 1 ? { ok: false, status: 503 } : { ok: true, json: async () => ({ articles: [{ article_id: 'recovered' }] }) });
  try {
    await assert.rejects(loadResearchCatalogue({ facets: { study: 'mathematical modelling' } }), /503/);
    assert.deepEqual((await loadResearchCatalogue({ facets: { study: 'mathematical modelling' } })).map(row => row.article_id), ['recovered']);
    assert.equal(attempts, 2);
  } finally { globalThis.fetch = originalFetch; }
});

test('Ask skips bibliography-only chunks while the full catalogue and legacy chunks remain available', async () => {
  const { loadResearchQuestionCatalogue, loadResearchCatalogue } = await import('../src/lib/research-catalogue-client.ts?reviewed-chunks');
  const originalFetch = globalThis.fetch, requests = [];
  const records = { '/reviewed.json': {article_id:'reviewed'}, '/bibliographic.json': {article_id:'bibliographic',detail_available:false}, '/legacy.json': {article_id:'legacy'} };
  globalThis.fetch = async path => {
    requests.push(path);
    const payload = path.endsWith('entities.json') ? {entities:[]} : path.endsWith('manifest.json') ? {chunks:[
      {path:'/reviewed.json',count:1,reviewed_count:1}, {path:'/bibliographic.json',count:1,reviewed_count:0}, {path:'/legacy.json',count:1},
    ]} : {articles:[records[path]]};
    return {ok:true,json:async()=>payload};
  };
  try {
    assert.deepEqual((await loadResearchQuestionCatalogue('unknown query')).map(x=>x.article_id),['reviewed','legacy']);
    assert.equal(requests.includes('/bibliographic.json'),false);
    assert.deepEqual((await loadResearchCatalogue({includeBibliographic:true})).map(x=>x.article_id),['reviewed','bibliographic','legacy']);
  } finally { globalThis.fetch=originalFetch; }
});

const nextTurn = () => new Promise(resolve => setImmediate(resolve));
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

test('cancelled callers stop before fetching or while waiting for a shared manifest', async () => {
  const { loadResearchCatalogue } = await import('../src/lib/research-catalogue-client.ts?abort-manifest');
  const originalFetch = globalThis.fetch, requests = [], manifest = deferred();
  const chunks = [{ path: '/after-manifest.json', count: 1 }];
  globalThis.fetch = async path => {
    requests.push(path);
    if (path === '/research/catalogue/manifest.json') return manifest.promise;
    return { ok: true, json: async () => ({ articles: [{ article_id: path }] }) };
  };
  try {
    const before = new AbortController(); before.abort();
    await assert.rejects(loadResearchCatalogue({ signal: before.signal }), { name: 'AbortError' });
    assert.deepEqual(requests, []);
    const during = new AbortController();
    const old = loadResearchCatalogue({ signal: during.signal });
    const rejected = assert.rejects(old, { name: 'AbortError' });
    const current = loadResearchCatalogue();
    during.abort();
    await rejected;
    assert.deepEqual(requests, ['/research/catalogue/manifest.json']);
    manifest.resolve({ ok: true, json: async () => ({ chunks }) });
    assert.deepEqual((await current).map(row => row.article_id), ['/after-manifest.json']);
    assert.equal(requests.length, 2, 'cancelling the manifest waiter does not invalidate the shared fetch');
  } finally { globalThis.fetch = originalFetch; }
});

test('changing filters abandons the old catalogue after its current batch and lets the new filter finish', async () => {
  const { loadResearchCatalogue, researchCatalogueConcurrency } = await import('../src/lib/research-catalogue-client.ts?abort-filter');
  const originalFetch = globalThis.fetch, requests = [], progress = [], gates = new Map();
  const chunks = Array.from({ length: 14 }, (_, index) => ({ path: `/abort-filter-${index}.json`, count: 1, facets: { country: [index < 12 ? 'old' : 'new'] } }));
  globalThis.fetch = async (path, options) => {
    if (path.endsWith('manifest.json')) return { ok: true, json: async () => ({ chunks }) };
    assert.equal(options?.signal, undefined, 'shared chunk fetches do not receive a caller signal');
    requests.push(path);
    if (chunks.findIndex(chunk => chunk.path === path) < 12) {
      const gate = deferred(); gates.set(path, gate); await gate.promise;
    }
    return { ok: true, json: async () => ({ articles: [{ article_id: path }] }) };
  };
  try {
    const controller = new AbortController();
    const old = loadResearchCatalogue({ facets: { country: 'old' }, signal: controller.signal, onProgress: event => progress.push(event) });
    const rejected = assert.rejects(old, { name: 'AbortError' });
    await nextTurn();
    assert.equal(requests.length, researchCatalogueConcurrency);
    controller.abort();
    await rejected; // Cancellation is immediate even when shared requests are still pending.
    const current = loadResearchCatalogue({ facets: { country: 'new' } });
    for (const gate of gates.values()) gate.resolve();
    assert.deepEqual((await current).map(row => row.article_id), chunks.slice(12).map(chunk => chunk.path));
    await nextTurn();
    assert.equal(requests.filter(path => chunks.slice(0, 12).some(chunk => chunk.path === path)).length, researchCatalogueConcurrency);
    assert.deepEqual(progress.map(event => event.completed), [0], 'abandoned calls emit no later progress');
  } finally { for (const gate of gates.values()) gate.resolve(); globalThis.fetch = originalFetch; }
});

test('cancelling one overlapping caller preserves shared chunks for the caller still using them', async () => {
  const { loadResearchCatalogue } = await import('../src/lib/research-catalogue-client.ts?abort-shared');
  const originalFetch = globalThis.fetch, requests = [], firstBatch = deferred();
  const chunks = Array.from({ length: 10 }, (_, index) => ({ path: `/abort-shared-${index}.json`, count: 1 }));
  globalThis.fetch = async path => {
    if (path.endsWith('manifest.json')) return { ok: true, json: async () => ({ chunks }) };
    requests.push(path);
    if (chunks.findIndex(chunk => chunk.path === path) < 4) await firstBatch.promise;
    return { ok: true, json: async () => ({ articles: [{ article_id: path }] }) };
  };
  try {
    const controller = new AbortController();
    const old = loadResearchCatalogue({ signal: controller.signal });
    const rejected = assert.rejects(old, { name: 'AbortError' });
    const current = loadResearchCatalogue();
    await nextTurn();
    assert.equal(requests.length, 4);
    controller.abort(); await rejected;
    firstBatch.resolve();
    assert.deepEqual((await current).map(row => row.article_id), chunks.map(chunk => chunk.path));
    assert.equal(requests.length, chunks.length, 'the surviving caller never refetches shared chunks');
    assert.equal(new Set(requests).size, chunks.length);
  } finally { firstBatch.resolve(); globalThis.fetch = originalFetch; }
});

test('a failed chunk stops sibling workers from scanning the remaining catalogue and permits retry', async () => {
  const { loadResearchCatalogue } = await import('../src/lib/research-catalogue-client.ts?fail-stop');
  const originalFetch = globalThis.fetch, requests = [], gates = new Map(), progress = [];
  const chunks = Array.from({ length: 13 }, (_, index) => ({ path: `/fail-stop-${index}.json`, count: 1 }));
  let firstAttempt = true;
  globalThis.fetch = async path => {
    if (path.endsWith('manifest.json')) return { ok: true, json: async () => ({ chunks }) };
    requests.push(path);
    if (firstAttempt) {
      const gate = deferred(); gates.set(path, gate);
      await gate.promise;
      if (path === chunks[0].path) return { ok: false, status: 503 };
    }
    return { ok: true, json: async () => ({ articles: [{ article_id: path }] }) };
  };
  try {
    const failed = assert.rejects(loadResearchCatalogue({ onProgress: event => progress.push(event) }), /503/);
    await nextTurn();
    assert.equal(requests.length, 4);
    gates.get(chunks[0].path).resolve();
    await failed;
    for (const gate of gates.values()) gate.resolve();
    await nextTurn();
    assert.equal(requests.length, 4, 'a failed call never schedules the rest of the catalogue');
    assert.deepEqual(progress.map(event => event.completed), [0]);
    firstAttempt = false;
    assert.deepEqual((await loadResearchCatalogue()).map(row => row.article_id), chunks.map(chunk => chunk.path));
    assert.equal(requests.length, chunks.length + 1, 'retry reuses successful chunks and retries the failed chunk');
  } finally { for (const gate of gates.values()) gate.resolve(); globalThis.fetch = originalFetch; }
});

test('Ask forwards cancellation to catalogue scanning without loading the remaining chunks', async () => {
  const { loadResearchQuestionCatalogue } = await import('../src/lib/research-catalogue-client.ts?abort-question');
  const originalFetch = globalThis.fetch, requests = [], firstBatch = deferred();
  const chunks = Array.from({ length: 9 }, (_, index) => ({ path: `/abort-question-${index}.json`, count: 1 }));
  globalThis.fetch = async path => {
    if (path.endsWith('entities.json')) return { ok: true, json: async () => ({ entities: [] }) };
    if (path.endsWith('manifest.json')) return { ok: true, json: async () => ({ chunks }) };
    requests.push(path); await firstBatch.promise;
    return { ok: true, json: async () => ({ articles: [{ article_id: path }] }) };
  };
  try {
    const controller = new AbortController();
    const rejected = assert.rejects(loadResearchQuestionCatalogue('unknown query', { signal: controller.signal }), { name: 'AbortError' });
    await nextTurn();
    assert.equal(requests.length, 4);
    controller.abort(); await rejected;
    firstBatch.resolve(); await nextTurn();
    assert.equal(requests.length, 4);
  } finally { firstBatch.resolve(); globalThis.fetch = originalFetch; }
});
