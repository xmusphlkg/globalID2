import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';

// Shards are content-addressed so the index binds every article byte.
const directory = resolve(process.argv[2] || 'dist', 'research');
const path = resolve(directory, 'evidence-manifest.json');
const manifest = JSON.parse(await readFile(path, 'utf8'));
if (manifest.schema_version !== 'gids.research-evidence.v1' || !Array.isArray(manifest.articles)) {
  throw new Error('Invalid research evidence manifest');
}
const shards = [];
await mkdir(resolve(directory, 'evidence-shards'), { recursive: true });
for (let i = 0; i < manifest.articles.length; i += 5000) {
  const articles = manifest.articles.slice(i, i + 5000);
  const bytes = Buffer.from(JSON.stringify(articles));
  const sha256 = createHash('sha256').update(bytes).digest('hex');
  const name = `evidence-shards/${sha256}.json`;
  await writeFile(resolve(directory, name), bytes);
  shards.push({ path: name, sha256, count: articles.length });
}
delete manifest.articles;
manifest.schema_version = 'gids.research-evidence.v2';
manifest.article_shards = shards;
await writeFile(path, JSON.stringify(manifest));
console.log(`[research-evidence] wrote ${shards.length} article shards`);
