import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { gzipSync } from 'node:zlib';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';
const hash = bytes => createHash('sha256').update(bytes).digest('hex');

test('real Worker assembles compressed objects and falls back on corruption', async () => {
 const runtime = new Miniflare(convertV4MiniflareOptions({ modules: true,
   scriptPath: resolve('.wrangler/test-bundle/index.js'), compatibilityDate: '2026-11-11',
   compatibilityFlags: ['nodejs_compat'], r2Buckets: ['DATA'] }));
 try {
  const bucket=await runtime.getR2Bucket('DATA'); const prefix='paper-template-v1';
  const put=async (group,value) => { const raw=Buffer.from(JSON.stringify(value));const sha=hash(raw);await bucket.put(`${prefix}/${group}/${sha}.json`,gzipSync(raw),{httpMetadata:{contentType:'application/json',contentEncoding:'gzip'}});return sha; };
  const slug='paper-1';
  const record=await put('records',{schema:1,slug,head:['<title>Article</title>','<meta name="description" content="Summary">'],main:'<main id="main-content"><h1>Evidence</h1></main>'});
  const template=await put('templates',{schema:1,chunks:['<html><head>',{field:'head',index:0},{field:'head',index:1},'</head><body>',{field:'main'},'</body></html>']});
  const index=await put('indexes',{schema:1,locale:'en',items:{[slug]:record}});
  const generation=await put('generations',{schema:1,locales:{en:{template,indexes:{[hash(slug).slice(0,2)]:index}}}});
  await bucket.put(`${prefix}/current.json`,JSON.stringify({schema:1,generation}));
  const url='https://papers.example/research/articles/paper-1/';
  const res=await runtime.dispatchFetch(url);assert.equal(res.status,200);assert.equal(res.headers.get('x-gids-paper-mode'),'template');
  assert.equal(await res.text(),'<html><head><title>Article</title><meta name="description" content="Summary"></head><body><main id="main-content"><h1>Evidence</h1></main></body></html>');
  const etag=res.headers.get('etag');assert.equal((await runtime.dispatchFetch(url,{headers:{'If-None-Match':etag}})).status,304);
  await bucket.put('research/articles/paper-1/','<html>old</html>',{httpMetadata:{contentType:'text/html'}});
  await bucket.put(`${prefix}/records/${record}.json`,'{}');
  const fallback=await runtime.dispatchFetch(url);assert.equal(fallback.headers.get('x-gids-paper-mode'),'legacy');assert.equal(await fallback.text(),'<html>old</html>');
 } finally { await runtime.dispose(); }
});

test('real published candidate returns byte-identical SEO pages in all locales', async (context) => {
 const candidate=process.env.GIDS_PAPERS_CANDIDATE, source=process.env.GIDS_PAPERS_SOURCE;
 if (!candidate || !source) return context.skip('Set the candidate and source directories to validate real pages');
 const {readFile} = await import('node:fs/promises');
 const {join} = await import('node:path');
 const runtime = new Miniflare(convertV4MiniflareOptions({ modules:true,scriptPath:resolve('.wrangler/test-bundle/index.js'),compatibilityDate:'2026-11-11',compatibilityFlags:['nodejs_compat'],r2Buckets:['DATA'] }));
 try {
  const bucket=await runtime.getR2Bucket('DATA'), prefix='paper-template-v1';
  const pointer=await readFile(join(candidate,'current.json')); await bucket.put(`${prefix}/current.json`,pointer);
  const generationHash=JSON.parse(pointer).generation;
  const readObject=async (kind,id) => { const raw=await readFile(join(candidate,kind,`${id}.json`));assert.equal(hash(raw),id);await bucket.put(`${prefix}/${kind}/${id}.json`,gzipSync(raw),{httpMetadata:{contentType:'application/json',contentEncoding:'gzip'}});return JSON.parse(raw); };
  const generation=await readObject('generations',generationHash);
  const slug='10-1001-jama-1997-03540480040036-57010812';
  for(const [locale,part] of [['en',''],['zh','zh/'],['fr','fr/']]) {
   const spec=generation.locales[locale];await readObject('templates',spec.template);
   const index=await readObject('indexes',spec.indexes[hash(slug).slice(0,2)]);await readObject('records',index.items[slug]);
   const res=await runtime.dispatchFetch(`https://papers.example/${part}research/articles/${slug}/`);
   assert.equal(res.status,200);assert.equal(res.headers.get('x-gids-paper-mode'),'template');
   const actual=Buffer.from(await res.arrayBuffer()), expected=await readFile(join(source,part,'research/articles',slug,'index.html'));
   assert.deepEqual(actual,expected);assert.match(actual.toString(),/<title>/);assert.match(actual.toString(),/rel="canonical"/);
  }
 } finally { await runtime.dispose(); }
});
