// Publishable content only: this entry point never invokes Astro or Vite.
import { changelogReleases } from '../src/data/changelog.ts';
import { readChangelog } from '../src/lib/changelog.ts';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

const value = { schema_version: 1, releases: changelogReleases };
readChangelog(value);
const engineeringNotes = resolve(import.meta.dirname, '../../CHANGELOG.md');
if (existsSync(engineeringNotes)) {
  const versions = new Set(changelogReleases.map(release => release.version));
  const missing = [...readFileSync(engineeringNotes, 'utf8').matchAll(/^## \[(\d+\.\d+\.\d+)\] - \d{4}-\d{2}-\d{2}\s*$/gm)]
    .map(match => match[1]).filter(version => !versions.has(version));
  if (missing.length) throw new Error(`Public changelog is missing versions: ${missing.join(', ')}`);
}
const body = `${JSON.stringify(value)}\n`;
if (process.argv.includes('--dev-output')) {
  const directory = resolve(import.meta.dirname, '../public/site-data/updates');
  mkdirSync(directory, { recursive: true });
  writeFileSync(resolve(directory, 'changelog.json'), body);
} else process.stdout.write(body);
