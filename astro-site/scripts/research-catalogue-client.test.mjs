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

test('focused Ask loads complete scoped records and retries failed collections', async () => {
  const { loadResearchQuestionCatalogue } = await import('../src/lib/research-catalogue-client.ts');
  const originalFetch = globalThis.fetch, requests = [];
  let failed = false;
  globalThis.fetch = async path => {
    requests.push(path);
    if (path === '/research/catalogue/entities.json') return { ok: true, json: async () => ({ entities: [
      { scope: 'disease', value: 'd038', aliases: ['flu', '流感'], path: '/site-data/research/diseases/d038.json' },
      { scope: 'disease', value: 'd029', aliases: ['measles'], path: '/site-data/research/diseases/d029.json' },
    ] }) };
    if (String(path).endsWith('/d038.json')) return { ok: true, json: async () => ({ articles: Array.from({ length: 87 }, (_, i) => ({ article_id: String(i) })) }) };
    if (String(path).endsWith('/d029.json') && !failed) { failed = true; return { ok: false }; }
    return { ok: true, json: async () => ({ articles: [{ article_id: 'recovered' }] }) };
  };
  try {
    assert.equal((await loadResearchQuestionCatalogue('日本流感')).length, 87);
    assert.equal((await loadResearchQuestionCatalogue('flu in Japan')).length, 87);
    assert.equal(requests.length, 2);
    assert.equal(requests.some(path => String(path).includes('/manifest.json')), false);
    await assert.rejects(loadResearchQuestionCatalogue('measles'));
    assert.equal((await loadResearchQuestionCatalogue('measles'))[0].article_id, 'recovered');
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

test('a complete 640-record study directory replaces hundreds of mixed chunks with seven immutable pages', async () => {
  const { loadResearchCatalogue } = await import('../src/lib/research-catalogue-client.ts?complete-study');
  const originalFetch = globalThis.fetch, requests = [], progress = [];
  const records = Array.from({ length: 640 }, (_, index) => ({ article_id: `model-${index}`, study_type: 'Mathematical modelling', detail_available: index !== 639 }));
  const pages = Array.from({ length: 7 }, (_, index) => ({ path: `/research/catalogue/studies/mathematical-modelling-all${index}-abcdef0123456789.json`, count: Math.min(100, 640 - index * 100) }));
  let active = 0, maximum = 0;
  globalThis.fetch = async path => {
    requests.push(path);
    if (path === '/research/catalogue/manifest.json') return { ok: true, json: async () => ({
      chunks: Array.from({ length: 482 }, (_, index) => ({ path: `/mixed-${index}.json`, count: 100, facets: { study: ['mathematical modelling'] } })),
      study_collections: { 'mathematical modelling': { count: 640, chunks: pages } },
    }) };
    const page = pages.findIndex(page => String(path).endsWith(page.path));
    assert.ok(page >= 0, `must not request mixed catalogue: ${path}`);
    maximum = Math.max(maximum, ++active); await new Promise(resolve => setTimeout(resolve, 2)); active--;
    return { ok: true, json: async () => ({ schema_version: 2, study: 'mathematical modelling', articles: records.slice(page * 100, (page + 1) * 100) }) };
  };
  try {
    const result = await loadResearchCatalogue({ facets: { study: 'Mathematical modelling', country: 'JP' }, includeBibliographic: true, onProgress: event => progress.push(event) });
    assert.deepEqual(result, records, 'complete study rows remain available for exact country filtering');
    assert.equal(requests.length, 8);
    assert.equal(maximum, 4);
    assert.deepEqual(progress.map(event => event.completed), [0, 1, 2, 3, 4, 5, 6, 7]);
    assert.ok(progress.every(event => event.total === 7));
    assert.equal((await loadResearchCatalogue({ scope: 'study', value: 'mathematical modelling' })).length, 639);
    assert.equal(requests.length, 8, 'study pages share the content-addressed cache');
  } finally { globalThis.fetch = originalFetch; }
});

test('study page generation preserves every record and large collections keep old months stable', async () => {
  const { buildResearchStudyCollections } = await import('../src/lib/research-catalogue-shards.ts');
  const models = Array.from({ length: 640 }, (_, index) => ({ article_id: `model-${index}`, study_type: 'Mathematical modelling', published_at: '2026-11-01' }));
  const study = buildResearchStudyCollections(models)[0];
  assert.equal(study.count, 640);
  assert.equal(study.chunks.length, 7);
  assert.ok(study.chunks.every(page => page.count <= 100));
  assert.deepEqual(new Set(study.chunks.flatMap(page => page.articles.map(article => article.article_id))), new Set(models.map(article => article.article_id)));
  const journal = Array.from({ length: 1100 }, (_, index) => ({ article_id: `journal-${index}`, study_type: 'Journal article', published_at: index < 1000 ? '2025-01-01' : '2026-11-01' }));
  journal.push({ article_id: 'undated', study_type: 'Journal article', published_at: '' });
  const before = buildResearchStudyCollections(journal)[0];
  const after = buildResearchStudyCollections([...journal, { article_id: 'added', study_type: 'Journal article', published_at: '2026-11-11' }])[0];
  const bucket = (group, prefix) => group.chunks.filter(chunk => chunk.path.includes(prefix));
  assert.deepEqual(bucket(before, '-202501'), bucket(after, '-202501'));
  assert.deepEqual(bucket(before, '-000000'), bucket(after, '-000000'));
  assert.notDeepEqual(bucket(before, '-202611'), bucket(after, '-202611'));
  assert.deepEqual(buildResearchStudyCollections([...journal].reverse())[0], before, 'source iteration order does not churn monthly pages');
  const atThreshold = buildResearchStudyCollections(journal.slice(0, 1000))[0];
  assert.equal(atThreshold.chunks.length, 10);
  assert.ok(atThreshold.chunks.every(page => page.path.includes('-all')));
  assert.ok(before.chunks.every(page => !page.path.includes('-all')));
});
