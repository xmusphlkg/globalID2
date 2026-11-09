import { readResearchIndex } from '../../lib/generated-site-data.ts';
import { buildResearchEvidenceManifest } from '../../lib/research-evidence-manifest.ts';
import { lstatSync, readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';

export const prerender = true;

export function GET() {
  const directory = resolve('./src/data/research/weekly');
  return new Response(JSON.stringify(buildResearchEvidenceManifest(readResearchIndex(), week => {
    const path = resolve(directory, `${week}.json`);
    if (!lstatSync(path).isFile()) throw new Error('research_evidence_week_invalid');
    return readFileSync(path, 'utf8');
  }, () => readdirSync(directory).filter(name => name.endsWith('.json')).map(name => name.slice(0, -5)))), {
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
  });
}
