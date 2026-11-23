import assert from 'node:assert/strict';
import test from 'node:test';
import { diseaseLiteratureSearchStrategy, emptyLiteratureFilters, filterLiterature, literatureArticle, literatureTrend, literatureYearRangeError, literatureIndexRecord, literaturePage } from './research-literature.ts';
import { researchQueryScopes, type ResearchQueryEntity } from './research-query-scope.ts';
const articles = [
  { title: 'Japan trial', published_at: '2026-03-01', countries: [{ code: 'JP' }], study_type: 'Trial', open_access_status: 'open', detail_available: true, topics: [{ name: 'Vaccination' }] },
  { title: 'Japan historical study', published_at: '2020-01-01', countries: [{ code: 'JP' }], detail_available: false },
  { title: 'Brazil trial', published_at: '2026-02-01', countries: [{ code: 'BR' }], study_type: 'Trial', open_access_status: 'open', detail_available: false },
  { title: 'Undated paper', countries: [{ code: 'JP' }] },
];
test('complete catalogue filters and trend apply the same combined criteria', () => {
  const filtered = filterLiterature(articles, { ...emptyLiteratureFilters, country: 'JP', from: '2025', to: '2026', access: 'open', study: 'Trial', topic: 'Vaccination' });
  assert.deepEqual(filtered.map(row => row.title), ['Japan trial']);
  assert.deepEqual(literatureTrend(filtered), [{ year: '2026', count: 1 }]);
  assert.deepEqual(literatureTrend(articles), [{ year: '2020', count: 1 }, { year: '2026', count: 2 }]);
  assert.equal(filterLiterature(articles, { ...emptyLiteratureFilters, evidence: 'bibliographic' }).length, 2);
  assert.equal(filterLiterature(articles, { ...emptyLiteratureFilters, query: 'Brazil trial' }).length, 1);
  assert.equal(filterLiterature(Array.from({ length: 71 }, (_, i) => ({ title: String(i) })), emptyLiteratureFilters).length, 71);
  assert.equal(filterLiterature(articles, { ...emptyLiteratureFilters, sort: 'oldest' }).at(-1).title, 'Undated paper');
});
test('bibliographic projection preserves source IDs without full summaries', () => {
  const row = literatureArticle({ pmid: '123', detail_available: false, summary: { en: { main_findings: 'large' } } });
  assert.equal(row.detail_available, false); assert.equal(row.pmid, '123'); assert.equal('summary' in row, false);
  const fullAuthors = literatureArticle({ authors: ['A', 'B', 'C', 'D', 'Fifth Author'], title: 'Study' });
  assert.equal(filterLiterature([fullAuthors], { ...emptyLiteratureFilters, query: 'Fifth Author' }).length, 1);
});
test('year filters reject partial, invalid and reversed bounds rather than silently comparing strings', () => {
  assert.equal(literatureYearRangeError({ from: '20', to: '' }), 'invalid');
  assert.equal(literatureYearRangeError({ from: '2026', to: '2020' }), 'reversed');
  assert.equal(literatureYearRangeError({ from: '2020', to: '2026' }), null);
  assert.deepEqual(filterLiterature(articles, { ...emptyLiteratureFilters, from: '20' }), []);
});
test('slim index preserves filtering and trends while pagination only slices matching record IDs', () => {
  const full = articles.map((article, i) => ({ ...article, article_id: String(i) }));
  const index = full.map(article => literatureIndexRecord(article, 'page-0'));
  const filters = { ...emptyLiteratureFilters, country: 'JP', from: '2025' };
  assert.deepEqual(filterLiterature(index, filters).map(row => row.literature_id), filterLiterature(full, filters).map(row => row.article_id));
  assert.deepEqual(literatureTrend(index), literatureTrend(full));
  assert.equal('authors' in index[0], false);
  assert.equal('title' in index[0], false);
  const rows = Array.from({ length: 53 }, (_, i) => ({ literature_id: String(i) }));
  assert.deepEqual(literaturePage(rows, 2).records.map(row => row.literature_id), rows.slice(24, 48).map(row => row.literature_id));
  assert.equal(literaturePage(rows, 100).page, 3);
});
test('external search strategies encode disease-specific title/abstract phrases', () => {
  const influenza = diseaseLiteratureSearchStrategy({ slug: 'influenza', name_en: 'Influenza' });
  assert.match(influenza.pubmed, /"flu"\[Title\/Abstract\]/);
  assert.equal(new URL(influenza.pubmedUrl).searchParams.get('term'), influenza.pubmed);
  assert.equal(new URL(influenza.europepmcUrl).searchParams.get('query'), influenza.europepmc);
  assert.deepEqual(diseaseLiteratureSearchStrategy({ slug: 'haemophilus-influenzae', name_en: 'Haemophilus influenzae' }).terms, ['Haemophilus influenzae']);
});
const entities: ResearchQueryEntity[] = [
  { scope: 'disease', value: 'd038', aliases: ['Influenza', 'flu', '流感'] },
  { scope: 'disease', value: 'd014', aliases: ['Avian influenza', '禽流感'] },
  { scope: 'country', value: 'jp', aliases: ['Japan', '日本'] },
];
test('Ask resolves CJK questions to scopes and rejects substring entity collisions', () => {
  assert.deepEqual(researchQueryScopes('日本流感', entities).map(x => x.value), ['d038']);
  assert.deepEqual(researchQueryScopes('Avian influenza in Japan', entities).map(x => x.value), ['d014']);
  assert.deepEqual(researchQueryScopes('Japan vaccination', entities).map(x => x.value), ['jp']);
  assert.deepEqual(researchQueryScopes('fluid dynamics', entities), []);
});

test('monthly immutable shards isolate additions, backfills and undated records', async () => {
  const { diseaseLiteratureShards } = await import('./research-literature-projection.ts');
  const rows = Array.from({ length: 50 }, (_, index) => ({ article_id: `old-${index}`, title: `Older ${index}`, published_at: `2026-10-${String(index % 28 + 1).padStart(2, '0')}` }));
  rows.push({ article_id: 'new', title: 'New', published_at: '2026-11-10' });
  rows.push({ article_id: 'unknown', title: 'Unknown', published_at: '' });
  const first = diseaseLiteratureShards('d038', rows);
  const added = diseaseLiteratureShards('d038', [...rows, { article_id: 'newer', title: 'Newer', published_at: '2026-11-11' }]);
  const bucket = (shards: typeof first, month: string) => shards.filter(shard => shard.id.startsWith(month));
  assert.deepEqual(bucket(added, '202610'), bucket(first, '202610'));
  assert.deepEqual(bucket(added, '000000'), bucket(first, '000000'));
  assert.notDeepEqual(bucket(added, '202611'), bucket(first, '202611'));
  const backfill = diseaseLiteratureShards('d038', [...rows, { article_id: 'backfill', published_at: '2026-10-01' }]);
  assert.deepEqual(bucket(backfill, '202611'), bucket(first, '202611'));
  assert.deepEqual(bucket(backfill, '000000'), bucket(first, '000000'));
  assert.deepEqual(diseaseLiteratureShards('d038', [...rows].reverse()), first, 'input reordering leaves shards unchanged');
  const unknown = diseaseLiteratureShards('d038', [...rows, { article_id: 'undated', published_at: 'unknown' }]);
  assert.deepEqual(bucket(unknown, '202610'), bucket(first, '202610'));
  assert.deepEqual(bucket(unknown, '202611'), bucket(first, '202611'));
  assert.equal(new Set(added.map(shard => shard.id)).size, added.length);
  for (const shard of added) { assert.ok(shard.count <= 24); assert.match(shard.id, /^\d+-[a-f0-9]{16}$/); }
  assert.notEqual(diseaseLiteratureShards('d100', rows)[0].url, first[0].url);
});

test('adding one current-month paper changes only its disease index and monthly card pages', async () => {
  const { diseaseLiteratureBundleData, diseaseLiteratureFromIndex } = await import('./research-literature-projection.ts');
  const article = (id: string, disease: string, date: string) => ({ article_id: id, title: id, published_at: date, peer_review_status: 'peer_reviewed', diseases: [{ disease_id: disease, slug: disease }] });
  const source = { last_updated: '2026-11-10', articles: [article('one', 'd038', '2026-11-10'), article('older', 'd038', '2026-10-10')], bibliographic_articles: [article('other', 'd100', '2026-11-10')] };
  const catalogue = () => diseaseLiteratureFromIndex(source);
  const before = catalogue();
  const flu = diseaseLiteratureBundleData('d038', before.byDisease.get('d038')!);
  const other = diseaseLiteratureBundleData('d100', before.byDisease.get('d100')!);
  source.last_updated = '2026-11-11';
  source.articles.push(article('new', 'd038', '2026-11-11'));
  const after = catalogue();
  const updated = diseaseLiteratureBundleData('d038', after.byDisease.get('d038')!);
  assert.notEqual(JSON.stringify(updated.index), JSON.stringify(flu.index));
  assert.equal(JSON.stringify(diseaseLiteratureBundleData('d100', after.byDisease.get('d100')!)), JSON.stringify(other));
  assert.deepEqual(updated.shards.filter(shard => shard.id.startsWith('202610')), flu.shards.filter(shard => shard.id.startsWith('202610')));
  assert.equal('generated_at' in updated.index, false);
});
