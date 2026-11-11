import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createResearchPublicationClient } from './research-publication.ts';

const sha = (value: string) => createHash('sha256').update(value).digest('hex');

test('one manifest pins the index and every referenced shard', async () => {
  const index = JSON.stringify({ articles: { sharded: true, kind: 'list', count: 1, chunks: [{ file: 'shards/a.json', count: 1 }] } });
  const shard = JSON.stringify({ records: [{ id: 1 }] });
  const documents: Record<string, string> = { 'index.json': index, 'shards/a.json': shard };
  const objects: Record<string, string> = {};
  const files = Object.fromEntries(Object.entries(documents).map(([name, value]) => {
    const hash = sha(value); objects[hash] = value;
    return [name, { url: `objects/${hash}.json`, sha256: hash, bytes: Buffer.byteLength(value) }];
  }));
  let manifestRequests = 0;
  const client = createResearchPublicationClient({
    manifestUrl: 'https://example.test/research/current.json', baseUrl: 'https://example.test/',
    digest: async body => createHash('sha256').update(new Uint8Array(body)).digest('hex'),
    fetcher: async url => {
      const path = new URL(String(url)).pathname;
      if (path.endsWith('/current.json')) {
        manifestRequests++;
        return Response.json({ schema_version: 1, release_id: 'one', files });
      }
      const key = path.split('/').pop()!.replace('.json', '');
      return new Response(objects[key] ?? '', { status: objects[key] ? 200 : 404 });
    },
  });
  const result = await client.index();
  assert.equal(result.articles.count, 1);
  assert.deepEqual(await client.field(result, 'articles'), [{ id: 1 }]);
  assert.equal((await client.manifest())?.release_id, 'one');
  assert.equal(manifestRequests, 1);
});

test('rejects a list whose referenced shard has the wrong record count', async () => {
  const index = JSON.stringify({ articles: { sharded: true, kind: 'list', count: 2, chunks: [{ file: 'shards/a.json', count: 2 }] } });
  const shard = JSON.stringify({ records: [{ id: 1 }] });
  const values: Record<string, string> = { 'index.json': index, 'shards/a.json': shard };
  const objects: Record<string, string> = {};
  const files = Object.fromEntries(Object.entries(values).map(([name, value]) => {
    const hash = sha(value); objects[hash] = value;
    return [name, { url: `objects/${hash}.json`, sha256: hash, bytes: Buffer.byteLength(value) }];
  }));
  const client = createResearchPublicationClient({ manifestUrl: 'https://example.test/current.json', baseUrl: 'https://example.test/',
    digest: async body => createHash('sha256').update(new Uint8Array(body)).digest('hex'),
    fetcher: async url => {
      const path = new URL(String(url)).pathname;
      if (path.endsWith('/current.json')) return Response.json({ schema_version: 1, release_id: 'one', files });
      return new Response(objects[path.split('/').pop()!.replace('.json', '')]);
    },
  });
  const result = await client.index();
  await assert.rejects(client.field(result, 'articles'), /Incomplete research list shard/);
});

test('paper URLs use the same pinned generation as the research index', async () => {
  const generation = 'c'.repeat(64);
  const client = createResearchPublicationClient({ manifestUrl: 'https://example.test/current.json', baseUrl: 'https://example.test/',
    fetcher: async () => Response.json({ schema_version: 1, release_id: 'one', files: {},
      papers: { papers_generation_sha256: generation } }),
  });
  assert.equal(await client.paper('/research/articles/example/'), `/research-publication/${generation}/research/articles/example/`);
  await assert.rejects(client.paper('/research/../secret'), /Invalid research paper path/);
});
