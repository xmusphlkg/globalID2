import test from 'node:test';
import assert from 'node:assert/strict';
import { createLiteraturePageClient, type LiteratureIndex } from './research-literature-client.ts';
import { literatureIndexRecord, literaturePage } from './research-literature.ts';

test('a page loads only referenced immutable shards, preserves record order, and reuses successful bodies', async () => {
  const articles = Array.from({ length: 60 }, (_, index) => ({ article_id: String(index), title: `Paper ${index}`, authors: ['A', 'B'] }));
  const shards = [articles.slice(0, 24), articles.slice(24, 48), articles.slice(48)];
  const requests: string[] = [];
  const index: LiteratureIndex = { schema_version: 2, disease_id: 'd038', total: 60, page_size: 24,
    records: articles.map((article, i) => literatureIndexRecord(article, `page-${Math.floor(i / 24)}`)),
    shards: shards.map((rows, i) => ({ id: `page-${i}`, url: `/pages/${i}.json`, count: rows.length })) };
  const client = createLiteraturePageClient({ fetcher: (async (url: string) => {
    requests.push(url); const page = Number(url.match(/(\d+)\.json$/)![1]);
    return { ok: true, json: async () => ({ schema_version: 2, disease_id: 'd038', shard: `page-${page}`, articles: shards[page] }) };
  }) as typeof fetch });
  assert.equal((await client.read(index, literaturePage(index.records, 1).records)).length, 24);
  assert.deepEqual(requests, ['/pages/0.json']);
  assert.deepEqual((await client.read(index, [index.records[25], index.records[3]])).map(row => row.article_id), ['25', '3']);
  assert.deepEqual(requests, ['/pages/0.json', '/pages/1.json']);
  await client.read(index, literaturePage(index.records, 1).records);
  assert.equal(requests.length, 2);
});

test('failed page bodies can be retried and transfer concurrency is bounded', async () => {
  let active = 0, maximum = 0, fail = true;
  const index: LiteratureIndex = { schema_version: 2, disease_id: 'd038', total: 8, page_size: 24,
    records: Array.from({ length: 8 }, (_, i) => ({ literature_id: String(i), shard: String(i) })),
    shards: Array.from({ length: 8 }, (_, i) => ({ id: String(i), url: `/pages/${i}`, count: 1 })) };
  const client = createLiteraturePageClient({ concurrency: 2, fetcher: (async (url: string) => {
    active++; maximum = Math.max(maximum, active);
    await new Promise(resolve => setTimeout(resolve, 5)); active--;
    const id = url.split('/').at(-1)!;
    if (fail && id === '0') { fail = false; return { ok: false }; }
    return { ok: true, json: async () => ({ schema_version: 2, disease_id: 'd038', shard: id, articles: [{ article_id: id }] }) };
  }) as typeof fetch });
  await assert.rejects(client.read(index, index.records));
  const rows = await client.read(index, index.records);
  assert.equal(rows.length, 8);
  assert.equal(maximum, 2);
});
