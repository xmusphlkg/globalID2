// Local Worker-reader verification. Does not run Vite/Astro or publish objects.
import { createHash } from 'node:crypto';
import { readFileSync, mkdirSync, writeFileSync, statSync } from 'node:fs';
import { resolve, dirname, relative, isAbsolute } from 'node:path';
import { templateArticleResponse } from '../../cloudflare/surveillance-data/src/paper-template-content.ts';
const args = process.argv.slice(2);
const option = name => { const index = args.indexOf(name); return index < 0 ? null : args[index + 1]; };
const candidatePath = option('--candidate');
if (!candidatePath) throw new Error('--candidate is required');
const candidate = JSON.parse(readFileSync(candidatePath, 'utf8'));
if (candidate.protocol !== 'paper-template-v3' || !/^[a-f0-9]{64}$/.test(candidate.generation)) throw new Error('Invalid candidate');
const source = new Map(candidate.template_objects.map(item => [item.key, item]));
const sha = raw => createHash('sha256').update(raw).digest('hex');
const env = { PAPERS_PROJECTION_V3: '1', DATA: { async get(key) {
  const item = source.get(key); if (!item) return null;
  const path = resolve(candidate.template_root, item.path), rel = relative(candidate.template_root, path);
  if (rel.startsWith('..') || isAbsolute(rel) || statSync(path).size > 2 * 1024 * 1024) throw new Error('Invalid object path');
  const raw = readFileSync(path);
  if (sha(raw) !== item.sha256 || raw.length !== item.bytes) throw new Error('Candidate dependency integrity failure');
  return { size: raw.length, httpMetadata: {}, body: new Response(raw).body };
} } };
const selected = option('--slug'), output = option('--output');
const entries = candidate.entries.filter(item => item.paper_template?.schema === 3 && (!selected || item.paper_template.slug === selected));
if (!entries.length) throw new Error('No projected articles selected');
const results = [];
for (const entry of entries) {
  const response = await templateArticleResponse(new Request(`https://papers.globalinfectiousdisease.com/${entry.logical_key}`), env, entry, entry.logical_key, candidate.generation);
  if (response.status !== 200 || response.headers.get('X-GIDS-Paper-Mode') !== 'projection-v3') throw new Error('Projection failed');
  const html = await response.text();
  const recordDescriptor = source.get(entry.key);
  const record = JSON.parse(readFileSync(resolve(candidate.template_root, recordDescriptor.path), 'utf8'));
  const summary = record.content.summary[entry.paper_template.locale];
  const escaped = value => value.replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
  for (const key of ['research_question', 'study_design', 'population_setting', 'main_findings', 'public_health_relevance', 'limitations', 'gids_interpretation']) {
    if (typeof summary[key] === 'string' && summary[key] && !html.includes(escaped(summary[key]))) throw new Error(`Missing complete localized body: ${entry.path} ${key}`);
  }
  if (!html.includes('rel="canonical"') || !html.includes('hreflang="fr"') || !html.includes('application/ld+json')) throw new Error('Missing SEO projection');
  results.push({ path: entry.path, sha256: sha(html), bytes: Buffer.byteLength(html), record_sha256: entry.paper_template.record_sha256 });
  if (output) { const path = resolve(output, entry.path); if (!path.startsWith(`${resolve(output)}/`)) throw new Error('Invalid preview path'); mkdirSync(dirname(path), { recursive: true }); writeFileSync(path, html); }
}
const receipt = { schema: 1, event: 'papers_projection_verified', generation: candidate.generation, verified_pages: results.length, astro_build_count: 0, pages: results };
if (output) writeFileSync(resolve(output, 'verification.json'), JSON.stringify(receipt));
console.log(JSON.stringify({ ...receipt, pages: undefined }));
