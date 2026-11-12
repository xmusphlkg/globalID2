// Repartition an already rendered candidate without rebuilding article HTML.
import { readFileSync, writeFileSync, unlinkSync } from 'node:fs';
import { resolve, basename } from 'node:path';
import { createHash } from 'node:crypto';
import { partitionResearchCatalogue } from '../src/lib/research-catalogue-partition.ts';

const directory = resolve(process.argv[2], 'research/catalogue');
const path = resolve(directory, 'manifest.json');
const manifest = JSON.parse(readFileSync(path, 'utf8'));
const files = manifest.chunks.map(chunk => {
  const name = basename(chunk.path);
  if (chunk.path !== '/research/catalogue/' + name || !/^\d+-[a-f0-9]{16}\.json$/.test(name)) throw new Error('Unsafe catalogue path');
  return resolve(directory, name);
});
const payloads = files.map(file => JSON.parse(readFileSync(file, 'utf8')));
const articles = payloads.flatMap(payload => payload.articles);
if (articles.length !== manifest.total) throw new Error('Catalogue count mismatch');
articles.sort((a,b) => String(b.published_at ?? '').localeCompare(String(a.published_at ?? '')));
const facets = articles => Object.fromEntries(['disease','country','topic','study','week'].map(key => [key, [...new Set(articles.flatMap(article => {
  if (key === 'disease') return (article.diseases ?? []).map(item => String(item.disease_id ?? '').toLowerCase());
  if (key === 'country') return (article.countries ?? []).map(item => String(item.code ?? '').toLowerCase());
  if (key === 'topic') return (article.topics ?? []).map(item => String(item.name ?? '').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,''));
  return [String(key === 'study' ? article.study_type ?? '' : article.publication_week ?? '').trim().toLowerCase()];
}).filter(Boolean))]]));
const chunks = partitionResearchCatalogue(articles);
const next = chunks.map((articles,index) => {
  const hash = createHash('sha256').update(JSON.stringify(articles)).digest('hex').slice(0,16);
  const name = `${index}-${hash}.json`;
  writeFileSync(resolve(directory,name), JSON.stringify({...payloads[0],articles}));
  return {path:'/research/catalogue/'+name,count:articles.length,reviewed_count:articles.filter(article=>article.detail_available!==false).length,facets:facets(articles)};
});
writeFileSync(path,JSON.stringify({...manifest,chunks:next}));
const kept = new Set(next.map(chunk=>resolve(directory,basename(chunk.path))));
if (!process.argv.includes('--retain-previous')) for(const file of files) if(!kept.has(file)) unlinkSync(file);
console.log(JSON.stringify({records:articles.length,previous_chunks:files.length,chunks:chunks.length,model_chunks:next.filter(x=>x.facets.study.some(v=>v.includes('mathematical'))).length}));
