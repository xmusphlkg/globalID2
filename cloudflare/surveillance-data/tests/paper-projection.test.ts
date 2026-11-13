import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { test } from 'node:test';
import worker from '../src/index.ts';
import { templateArticleResponse } from '../src/paper-template-content.ts';
import { projectionFragments, type ProjectedPaper, type ProjectionTemplate } from '../src/paper-projection.ts';
const sha = (value: string) => createHash('sha256').update(value).digest('hex');
const record: ProjectedPaper = { schema: 3, slug: 'article-a', article_id: 'lit-a', content: {
  title: '<script>alert(1)</script> Paper', editorial_status: 'published', indexable: true,
  authors: ['Author'], summary: { en: { main_findings: '<img onerror=alert(1)> Findings', provenance: { editorially_approved: false } }, zh: { main_findings: '中文发现' }, fr: { main_findings: 'Résultats français' } },
  source_urls: { doi: 'https://doi.org/10.1/paper', publisher: 'javascript:alert(1)' },
} };
function template(locale: 'en' | 'zh' | 'fr', footer = 'one'): ProjectionTemplate {
  return { schema: 3, content_schema: 3, renderer: 'gids-evidence-v1', locale, scope_attributes: ['data-astro-cid-abcdef12'],
    shell: { schema: 1, chunks: [`<!doctype html><html lang="${locale}"><head>`, { field: 'head', index: 0 }, '</head><body>', { field: 'main' }, `<footer>${footer}</footer></body></html>`] } };
}
function fixture() {
  const objects = new Map<string, string>(), reads: string[] = [];
  const put = (group: string, value: unknown) => { const raw = JSON.stringify(value), hash = sha(raw); objects.set(`paper-template-v3/${group}/${hash}.json`, raw); return hash; };
  const env = { PAPERS_PROJECTION_V3: '1', DATA: { async get(key: string) {
    reads.push(key); const raw = objects.get(key); if (!raw) return null;
    return { size: Buffer.byteLength(raw), body: new Response(raw).body, httpMetadata: {}, text: async () => raw, json: async () => JSON.parse(raw) };
  } } } as unknown as Env;
  const recordSha = put('records', record), assetsSha = put('assets', { schema: 1, items: [] });
  function entry(locale: 'en' | 'zh' | 'fr', footer = 'one') {
    const templateSha = put('templates', template(locale, footer));
    return { logical_key: `${locale === 'en' ? '' : `${locale}/`}research/articles/article-a/`,
      key: `paper-template-v3/records/${recordSha}.json`, integrity: 'independent-v3', content_type: 'text/html; charset=utf-8',
      paper_template: { schema: 3, locale, slug: 'article-a', template_sha256: templateSha, record_sha256: recordSha, assets_sha256: assetsSha } };
  }
  function activate(entry: ReturnType<typeof fixture>['entry'] extends (...args: never[]) => infer T ? T : never) {
    const shard = JSON.stringify({ schema_version: 1, items: [entry] });
    const key = `releases/sha256/catalogue-shards/${sha(shard)}.json`; objects.set(key, shard);
    const generation = sha(JSON.stringify(entry));
    objects.set(`releases/sha256/catalogues/${generation}.json`, JSON.stringify({ schema_version: 2, generation_sha256: generation, shards: { [sha(entry.logical_key).slice(0, 2)]: { key } } }));
    objects.set('site-data/research-publication/current.json', JSON.stringify({ schema_version: 1, release_id: 'projection-test', papers: { papers_generation_sha256: generation } }));
    return generation;
  }
  return { objects, reads, env, put, entry, activate, recordSha, assetsSha };
}

test('structured projection escapes text, rejects active links, and preserves canonical/alternate/JSON-LD', () => {
  const fragments = projectionFragments(template('en'), record, sha('record'));
  assert.match(fragments.main, /&lt;img onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(fragments.main, /href="javascript:/);
  assert.match(fragments.main, /data-astro-cid-abcdef12/);
  assert.match(fragments.head[0], /hreflang="fr"/);
  assert.match(fragments.head[0], /rel="canonical" href="https:\/\/papers.globalinfectiousdisease.com\/research\/articles\/article-a\//);
  assert.match(fragments.head[0], /\\u003cscript\\u003e/);
});

test('all three locales read v3 from the same research pointer with independent hash headers', async () => {
  const f = fixture();
  for (const locale of ['en', 'zh', 'fr'] as const) {
    const entry = f.entry(locale); f.activate(entry);
    const response = await worker.fetch(new Request(`https://papers.globalinfectiousdisease.com/${entry.logical_key}`), f.env);
    assert.equal(response.status, 200); assert.equal(response.headers.get('X-GIDS-Paper-Mode'), 'projection-v3');
    assert.equal(response.headers.get('X-GIDS-Paper-Content-SHA256'), f.recordSha);
    assert.equal(response.headers.get('X-GIDS-Paper-SHA256'), null);
    assert.equal(response.headers.get('X-GIDS-Paper-Assets-SHA256'), f.assetsSha);
    assert.match(await response.text(), new RegExp(`<html lang="${locale}">`));
  }
  assert.equal(f.reads.filter(key => key.endsWith('/current.json')).length, 3);
});

test('v3 is off by default and corrupt records or asset manifests fail closed', async () => {
  const f = fixture(), entry = f.entry('en'); f.activate(entry);
  const request = new Request(`https://site.test/${entry.logical_key}`);
  assert.equal((await worker.fetch(request, { ...f.env, PAPERS_PROJECTION_V3: '0' })).status, 503);
  f.objects.set(`paper-template-v3/assets/${f.assetsSha}.json`, JSON.stringify({ schema: 1, items: [], tampered: true }));
  assert.equal((await worker.fetch(request, f.env)).status, 503);
});

test('response cache identity changes for template, assets and locale without changing content', async t => {
  const stored = new Map<string, Response>();
  const original = Object.getOwnPropertyDescriptor(globalThis, 'caches');
  Object.defineProperty(globalThis, 'caches', { configurable: true, value: { default: {
    async match(request: Request) { return stored.get(request.url)?.clone(); },
    async put(request: Request, response: Response) { stored.set(request.url, response.clone()); },
  } } });
  t.after(() => { if (original) Object.defineProperty(globalThis, 'caches', original); else Reflect.deleteProperty(globalThis, 'caches'); });
  const f = fixture(), first = f.entry('en'), second = f.entry('en', 'two'), french = f.entry('fr');
  const generation = sha('one-generation');
  const render = (entry: typeof first) => templateArticleResponse(new Request(`https://site.test/${entry.logical_key}`), f.env, entry, entry.logical_key, generation);
  const response1 = await render(first), response2 = await render(second), response3 = await render(french);
  assert.notEqual(response1.headers.get('etag'), response2.headers.get('etag'));
  assert.notEqual(response2.headers.get('etag'), response3.headers.get('etag'));
  assert.match(await response2.text(), /<footer>two<\/footer>/);
  const changed = structuredClone(first); changed.paper_template.assets_sha256 = f.put('assets', { schema: 1, items: [], revision: 2 });
  const response4 = await render(changed); assert.notEqual(response4.headers.get('etag'), response1.headers.get('etag'));
  assert.equal([...stored.keys()].filter(key => key.includes('/.paper-projection-response/')).length, 4);
  const head = await templateArticleResponse(new Request(`https://site.test/${first.logical_key}`, { method: 'HEAD' }), f.env, first, first.logical_key, generation);
  assert.equal(await head.text(), ''); assert.equal(head.headers.get('etag'), response1.headers.get('etag'));
});

test('editorial approval is locale-specific, unknown peer review is not a preprint, and topic links use stable slugs', () => {
  const item = structuredClone(record);
  item.content.peer_review_status = 'unknown';
  item.content.summary = { en: { provenance: { editorially_approved: true } }, zh: { provenance: { editorially_approved: false } }, fr: {} };
  item.content.summary_provenance = { editorially_approved: true }; // An obsolete/global field must not grant approval.
  item.content.topics = [{ name: 'Vaccine effectiveness', name_zh: '疫苗有效性', name_fr: 'Efficacité vaccinale' }];
  const english = projectionFragments(template('en'), item, sha('record')).main;
  const chinese = projectionFragments(template('zh'), item, sha('record')).main;
  const french = projectionFragments(template('fr'), item, sha('record')).main;
  assert.match(english, /Editorially approved/);
  assert.match(chinese, /尚未通过编辑审核/);
  assert.match(french, /non approuvé par la rédaction/);
  assert.match(english, /Peer review status unknown/);
  assert.doesNotMatch(english, /<span[^>]*>Preprint<\/span>/);
  assert.match(chinese, /\/zh\/research\/topics\/vaccine-effectiveness\//);
  assert.match(french, /\/fr\/research\/topics\/vaccine-effectiveness\//);
  assert.doesNotMatch(chinese, /\/topics\/\//);
});

test('Situation groups preserve exact versus historical context, deduplicate events, and keep expandable geography and trends', () => {
  const item = structuredClone(record);
  const signal = { signal_id: 'event-1', disease_id: 'covid', disease_name_en: 'COVID-19', disease_name_zh: '新冠',
    relation_level: 'exact_disease_geography', situation_url: '/situation/outbreaks/one/', data_through: '2026-11-12',
    geographies: [{ code: 'JP', name_en: 'Japan', name_zh: '日本' }], window: { change_pct: 12.5 } };
  item.content.related_signals = [signal, signal, { ...signal, signal_id: 'event-2', relation_level: 'historical_disease_geography_context', window: { change_pct: null } }];
  const chinese = projectionFragments(template('zh'), item, sha('record')).main;
  assert.equal((chinese.match(/<details /g) || []).length, 2);
  assert.match(chinese, /疾病 \+ 地区精确匹配/);
  assert.match(chinese, /疾病 \+ 地区历史背景/);
  assert.equal((chinese.match(/1 条信号/g) || []).length, 2);
  assert.match(chinese, /日本/);
  assert.match(chinese, /\+12.5%/);
  assert.match(chinese, /\/zh\/situation\/outbreaks\/one\//);
  assert.match(chinese, /<summary/);
});
