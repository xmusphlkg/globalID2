// Rebuild independent Research collections from this template build's frozen inputs.
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { buildResearch } from './build-research.mjs';
import { readResearchPublicFields } from '../src/lib/research-public-source.ts';
import { diseaseLiteratureFromIndex } from '../src/lib/research-literature-projection.ts';

const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const prefixes = ['', 'zh/', 'fr/'];
const segment = value => {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_()-]*$/.test(value))
    throw new Error('Invalid current Research collection segment');
  return value;
};

export function expectedCollectionPaths(publicRoot) {
  const index = readResearchPublicFields(publicRoot, ['facets', 'articles', 'bibliographic_articles', 'preprints']);
  const routes = [];
  const add = (group, slug) => {
    for (const prefix of prefixes) routes.push(`${prefix}research/${group}/${segment(slug)}/index.html`);
  };
  for (const [group, facet] of [['countries', 'countries'], ['topics', 'topics'], ['weekly', 'weeks']]) {
    for (const item of index.facets?.[facet] ?? []) add(group, group === 'weekly' ? item.week : item.slug);
  }
  // Curated facets omit bibliographic-only diseases. Use the renderer's exact projection.
  for (const facet of diseaseLiteratureFromIndex(index).diseaseFacets.values()) add('diseases', facet.slug);
  return [...new Set(routes)].sort();
}

function mergeResources(source, target) {
  if (!existsSync(source)) return;
  for (const entry of readdirSync(source, { withFileTypes: true })) {
    const from = resolve(source, entry.name), to = resolve(target, entry.name);
    if (entry.isSymbolicLink()) throw new Error('Symlink in Research build resources');
    if (entry.isDirectory()) mergeResources(from, to);
    else {
      if (existsSync(to) && digest(readFileSync(to)) !== digest(readFileSync(from)))
        throw new Error(`Research resource collides with the main template build: ${entry.name}`);
      if (!existsSync(to)) { mkdirSync(dirname(to), { recursive: true }); cpSync(from, to); }
    }
  }
}

export function buildSiteResearchCollections({ site, build = buildResearch }) {
  site = resolve(site);
  const publicRoot = resolve(site, 'public/site-data/research');
  const indexPath = resolve(publicRoot, 'index.json');
  const before = digest(readFileSync(indexPath));
  const expected = expectedCollectionPaths(publicRoot);
  const cache = resolve(site, '../data/cache');
  mkdirSync(cache, { recursive: true });
  const temporary = mkdtempSync(resolve(cache, 'site-collections-'));
  const snapshot = resolve(temporary, 'snapshot'), stage = resolve(temporary, 'stage');
  try {
    for (const [kind, source] of [['build', resolve(site, 'src/data/research')], ['public', publicRoot]]) {
      mkdirSync(resolve(snapshot, kind), { recursive: true });
      symlinkSync(source, resolve(snapshot, kind, 'research'), 'dir');
    }
    build({ site, project: resolve(site, '..'), snapshot, destination: stage,
      work: resolve(temporary, 'work'), scope: 'collections' });
    const scoped = JSON.parse(readFileSync(`${stage}.inputs.json`, 'utf8'));
    if (scoped.scope !== 'collections' || scoped.article_pages_rendered !== 0)
      throw new Error('Independent Research collection build has an invalid scope receipt');
    const pages = {};
    for (const path of expected) {
      const file = resolve(stage, path);
      if (!existsSync(file)) throw new Error(`Research collection build omitted current route: ${path}`);
      pages[path] = digest(readFileSync(file));
    }
    if (digest(readFileSync(indexPath)) !== before)
      throw new Error('Research index changed during collection rendering');
    const dist = resolve(site, 'dist');
    // Validate every current route before replacing any main-build Research tree.
    for (const prefix of prefixes) {
      const relative = `${prefix}research`;
      if (!existsSync(resolve(stage, relative, 'index.html')))
        throw new Error('Research collections build omitted a locale landing page');
    }
    for (const directory of ['_astro', 'scripts']) mergeResources(resolve(stage, directory), resolve(dist, directory));
    for (const prefix of prefixes) {
      const relative = `${prefix}research`;
      rmSync(resolve(dist, relative), { force: true, recursive: true });
      cpSync(resolve(stage, relative), resolve(dist, relative), { recursive: true });
    }
    const receipt = { schema: 1, scope: 'collections', article_pages_rendered: 0,
      research_index_sha256: before, pages };
    writeFileSync(resolve(dist, 'research-collections-build.json'), JSON.stringify(receipt));
    console.log(JSON.stringify({ event: 'site_research_collections_built', routes: expected.length,
      article_pages_rendered: 0, research_index_sha256: before }));
    return receipt;
  } finally { rmSync(temporary, { recursive: true, force: true }); }
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url)
  buildSiteResearchCollections({ site: resolve(import.meta.dirname, '..') });
