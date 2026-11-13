// Compile only country homepage routes, then assemble a reviewable Pages
// candidate over a frozen baseline. No other route enters Astro's module graph.
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync, constants } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { dirname, isAbsolute, relative, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { openDirectoryBuildCache } from './directory-build-cache.mjs';
import { inputSignature, factDigest } from '../src/lib/directory-facts.ts';

const LOCALES = ['', 'zh/', 'fr/'];
const ROUTES = LOCALES.map(locale => `${locale}countries/[country]/index.astro`);
const treeSignatures = root => Object.fromEntries(files(root).map(path => [relative(root, path), inputSignature(path)]));
const sha = body => createHash('sha256').update(body).digest('hex');
const contains = (parent, child) => { const rel = relative(resolve(parent), resolve(child)); return rel === '' || (!rel.startsWith('..') && !isAbsolute(rel)); };
const files = root => existsSync(root) ? readdirSync(root, { withFileTypes: true }).flatMap(entry => {
  const path = resolve(root, entry.name);
  if (entry.isSymbolicLink()) throw new Error(`Unexpected symlink in candidate inputs: ${path}`);
  return entry.isDirectory() ? files(path) : entry.isFile() ? [path] : [];
}) : [];
const copy = (from, to) => { mkdirSync(dirname(to), { recursive: true }); cpSync(from, to, { recursive: true, mode: constants.COPYFILE_FICLONE }); };
const htmlHashes = root => Object.fromEntries(files(root).filter(path => path.endsWith('.html')).map(path => [relative(root, path), sha(readFileSync(path))]));

export function countryBaselineDigest(root) {
  const digest = createHash('sha256');
  // Python's sorted(Path(...)) compares path components. A raw JS string sort
  // puts `a-b.html` before `a/z.html`, unlike the release pipeline's ordering.
  const paths = files(resolve(root)).filter(path => relative(resolve(root), path) !== 'release.json').sort((left, right) => {
    const a = relative(resolve(root), left).split('/'), b = relative(resolve(root), right).split('/');
    for (let index = 0; index < Math.min(a.length, b.length); index++) {
      const order = Buffer.compare(Buffer.from(a[index]), Buffer.from(b[index]));
      if (order) return order;
    }
    return a.length - b.length;
  });
  if (!paths.length) throw new Error('Country baseline must not be empty');
  for (const path of paths) {
    digest.update(relative(resolve(root), path)); digest.update('\0');
    digest.update(createHash('sha256').update(readFileSync(path)).digest());
  }
  return digest.digest('hex');
}

export function selectedCountryPaths(metadata, countries = ['all']) {
  const known = [...new Set((metadata.countries ?? []).map(country => String(country.code).toLowerCase()))].sort();
  const selected = countries.length === 1 && countries[0] === 'all' ? known : [...new Set(countries.map(code => code.toLowerCase()))].sort();
  if (!selected.length || selected.some(code => !/^[a-z0-9_-]+$/.test(code) || !known.includes(code))) throw new Error('Unknown or empty country template scope');
  return { selected, known, paths: selected.flatMap(code => LOCALES.map(locale => `${locale}countries/${code}/index.html`)) };
}

function sourceFingerprint(site) {
  const roots = ['src/components', 'src/layouts', 'src/lib', 'src/utils', 'src/styles', 'src/assets'];
  const inputs = roots.flatMap(path => files(resolve(site, path)));
  for (const path of [...ROUTES.map(path => `src/pages/${path}`), 'src/data/meta.json', 'src/data/reports/index.json',
    'src/data/diseases/index.json', 'src/data/changelog.ts', 'package.json', 'package-lock.json', 'astro.config.mjs',
    'postcss.config.mjs', 'tailwind.config.mjs', 'tsconfig.json', 'scripts/build-country-template.mjs',
    'scripts/directory-build-cache.mjs', 'scripts/file-fingerprints.mjs']) {
    if (existsSync(resolve(site, path))) inputs.push(resolve(site, path));
  }
  inputs.push(...files(resolve(site, '../shared')));
  const environment = Object.fromEntries(Object.entries(process.env).filter(([key]) => key.startsWith('PUBLIC_')
    || ['GLOBALID_SITE_URL', 'SITE_URL', 'SUBSCRIPTIONS__PUBLIC_BASE_URL', 'GIDS_RESEARCH_PUBLICATION_ENABLED'].includes(key)).sort());
  return sha(JSON.stringify({ files: inputs.sort().map(path => [relative(site, path), sha(readFileSync(path))]), environment, node: process.version }));
}

export function prepareCountryWorkspace({ site, work, countries = ['all'] }) {
  site = resolve(site); work = resolve(work);
  if (contains(site, work) || contains(work, site)) throw new Error('Country workspace must be outside the source site');
  const dataRoot = resolve(site, 'src/data');
  const metadata = JSON.parse(readFileSync(resolve(dataRoot, 'meta.json'), 'utf8'));
  const scope = selectedCountryPaths(metadata, countries);
  const before = sourceFingerprint(site);
  // The compact directory receipt prevents rendering a stable country shell
  // from parsing historical observations. Cache invalidation still uses each
  // source file's verified digest and stat identity.
  const directory = openDirectoryBuildCache(dataRoot);
  for (const code of scope.known) directory.country(code);
  directory.save();
  const facts = JSON.parse(readFileSync(directory.path, 'utf8'));
  rmSync(work, { recursive: true, force: true });
  const buildRoot = resolve(work, 'site'), src = resolve(buildRoot, 'src'), data = resolve(src, 'data');
  mkdirSync(resolve(src, 'pages'), { recursive: true });
  mkdirSync(data, { recursive: true });
  mkdirSync(resolve(buildRoot, 'public'), { recursive: true });
  copy(resolve(site, '../shared'), resolve(work, 'shared'));
  for (const name of ['package.json', 'astro.config.mjs', 'postcss.config.mjs', 'tailwind.config.mjs', 'tsconfig.json']) {
    if (existsSync(resolve(site, name))) copy(resolve(site, name), resolve(buildRoot, name));
  }
  symlinkSync(resolve(site, 'node_modules'), resolve(buildRoot, 'node_modules'), 'dir');
  for (const entry of readdirSync(resolve(site, 'src'), { withFileTypes: true })) {
    if (entry.name === 'pages' || entry.name === 'data') continue;
    copy(resolve(site, 'src', entry.name), resolve(src, entry.name));
  }
  for (const entry of readdirSync(dataRoot, { withFileTypes: true })) {
    const from = resolve(dataRoot, entry.name), to = resolve(data, entry.name);
    if (entry.isFile()) copy(from, to);
    else if (entry.isDirectory()) symlinkSync(from, to, 'dir');
  }
  for (const route of ROUTES) copy(resolve(site, 'src/pages', route), resolve(src, 'pages', route));
  // Preserve inode identities through data-directory symlinks while binding the
  // receipt to the isolated root expected by seo-page-data's loaders.
  facts.dataRoot = data;
  for (const entry of Object.values(facts.entries)) for (const input of entry.inputs)
    input.path = resolve(data, relative(dataRoot, input.path));
  const factsPath = resolve(work, 'directory-facts.json');
  writeFileSync(factsPath, JSON.stringify(facts));
  const plan = resolve(work, 'country-plan.json');
  writeFileSync(plan, JSON.stringify({ selected: scope.selected.map(code => `country:${code}`), known: scope.known.map(code => `country:${code}`) }));
  return { buildRoot, data, scope, before, facts, factsPath, plan, directoryStats: directory.stats };
}

export function mergeCountryOutput({ baseline, output, destination, expectedPaths }) {
  baseline = resolve(baseline); output = resolve(output); destination = resolve(destination);
  if (!existsSync(baseline) || existsSync(destination) || contains(baseline, destination) || contains(destination, baseline)
    || contains(output, destination) || contains(destination, output)) throw new Error('Country candidate needs distinct existing baseline and new output directories');
  const previous = htmlHashes(baseline), expected = new Set(expectedPaths);
  for (const path of expected) if (!(path in previous)) throw new Error(`New country routes require a routing publication: ${path}`);
  const rendered = htmlHashes(output);
  if (Object.keys(rendered).length !== expected.size || Object.keys(rendered).some(path => !expected.has(path))) throw new Error('Country build rendered an unexpected or incomplete route set');
  const emitted = files(output);
  for (const path of emitted) {
    const rel = relative(output, path);
    if (!expected.has(rel) && !rel.startsWith('_astro/')) throw new Error(`Unexpected country build artifact: ${rel}`);
    if (rel.startsWith('_astro/') && existsSync(resolve(baseline, rel))
      && sha(readFileSync(path)) !== sha(readFileSync(resolve(baseline, rel)))) throw new Error(`Immutable asset collision: ${rel}`);
  }
  copy(baseline, destination);
  for (const path of emitted) copy(path, resolve(destination, relative(output, path)));
  const after = htmlHashes(destination);
  const preserved = Object.keys(previous).filter(path => !expected.has(path));
  if (preserved.some(path => previous[path] !== after[path])) throw new Error('Unrelated baseline HTML changed during country assembly');
  for (const path of expected) {
    const body = readFileSync(resolve(destination, path), 'utf8');
    for (const match of body.matchAll(/(?:["'(=])\/?(_astro\/[^"'<>\s)]+)/g)) {
      const asset = match[1].split(/[?#]/)[0];
      if (!existsSync(resolve(destination, asset))) throw new Error(`Country HTML has a missing asset: ${asset}`);
    }
  }
  return { html_assembly_count: expected.size, preserved_html_count: preserved.length,
    new_asset_count: emitted.filter(path => relative(output, path).startsWith('_astro/') && !existsSync(resolve(baseline, relative(output, path)))).length,
    baseline_html_digest: sha(JSON.stringify(previous)), preserved_html_digest: sha(JSON.stringify(preserved.map(path => [path, previous[path]]))) };
}

export function buildCountryTemplate({ site, baseline, destination, work, countries = ['all'], expectedBaselineDigest, expectedBaselineReleaseSha256, run = spawnSync }) {
  const started = performance.now();
  site = resolve(site); baseline = resolve(baseline); destination = resolve(destination); work = resolve(work);
  if (existsSync(destination) || contains(work, baseline) || contains(baseline, work) || contains(site, destination)
    || contains(work, destination) || contains(destination, work)) throw new Error('Country build paths overlap or candidate already exists');
  const releaseFile = resolve(baseline, 'release.json');
  const baselineReleaseSha256 = existsSync(releaseFile) ? sha(readFileSync(releaseFile)) : null;
  if (expectedBaselineReleaseSha256 !== undefined && expectedBaselineReleaseSha256 !== baselineReleaseSha256) throw new Error('Country release identity differs from the frozen publication plan');
  const baselineArtifactDigest = countryBaselineDigest(baseline);
  if (expectedBaselineDigest !== undefined && expectedBaselineDigest !== baselineArtifactDigest) throw new Error('Country baseline differs from the frozen publication plan');
  const baselineBefore = htmlHashes(baseline);
  const baselineFilesBefore = treeSignatures(baseline);
  const prepared = prepareCountryWorkspace({ site, work, countries });
  for (const path of prepared.scope.paths) if (!(path in baselineBefore)) throw new Error(`Missing baseline country route: ${path}`);
  const env = { ...process.env, GIDS_COUNTRY_BUILD_DIR: prepared.buildRoot, GIDS_STATIC_RENDER: '1',
    GIDS_PAGE_BUILD_PLAN: prepared.plan, GIDS_SITE_DIRECTORY_FACTS: prepared.factsPath,
    GIDS_SITE_DIRECTORY_VERSION: prepared.facts.version };
  for (const key of ['GIDS_RESEARCH_BUILD_DIR', 'GIDS_REPORTS_BUILD_DIR', 'GIDS_RESEARCH_BUILD_PLAN', 'GIDS_BUILD_PUBLIC_DIR']) delete env[key];
  const compileStarted = performance.now();
  const result = run(resolve(site, 'node_modules/.bin/astro'), ['build'], { cwd: prepared.buildRoot, env, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`Country template compilation failed (${result.status})`);
  const compileSeconds = (performance.now() - compileStarted) / 1000;
  if (sourceFingerprint(site) !== prepared.before) throw new Error('Country template inputs changed during build');
  for (const entry of Object.values(prepared.facts.entries)) for (const input of entry.inputs)
    if (inputSignature(input.path) !== input.stat || factDigest(entry.value) !== entry.digest) throw new Error('Country directory snapshot changed during build');
  if (JSON.stringify(htmlHashes(baseline)) !== JSON.stringify(baselineBefore)) throw new Error('Baseline HTML changed during build');
  const assemblyStarted = performance.now();
  const merged = mergeCountryOutput({ baseline, output: resolve(prepared.buildRoot, 'dist'), destination, expectedPaths: prepared.scope.paths });
  if (JSON.stringify(treeSignatures(baseline)) !== JSON.stringify(baselineFilesBefore)) throw new Error('Baseline files changed during country build');
  const receipt = { schema: 1, schema_version: 1, kind: 'template', family: 'country', countries: prepared.scope.selected,
    baseline_artifact_digest: baselineArtifactDigest, baseline_release_sha256: baselineReleaseSha256, pages: prepared.scope.paths, source_input_digest: prepared.before, content_render_count: 0, country_html_render_count: prepared.scope.paths.length,
    papers_render_count: 0, reports_render_count: 0, template_compile_count: 1, pages_deployment_required: true,
    ...merged, directory_cache: prepared.directoryStats, compile_seconds: compileSeconds,
    assembly_seconds: (performance.now() - assemblyStarted) / 1000, elapsed_seconds: (performance.now() - started) / 1000 };
  writeFileSync(`${destination}.receipt.json`, JSON.stringify(receipt, null, 2));
  writeFileSync(resolve(destination, 'country-template-receipt.json'), JSON.stringify(receipt, null, 2));
  return receipt;
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  const args = Object.fromEntries(process.argv.slice(2).reduce((pairs, argument, index, all) => {
    if (argument.startsWith('--')) pairs.push([argument.slice(2), all[index + 1]]); return pairs;
  }, []));
  if (!args.baseline || !args.output) throw new Error('Usage: --baseline <dist> --output <new candidate> [--countries jp,ca|all]');
  const site = resolve(import.meta.dirname, '..'), project = resolve(site, '..'), cache = resolve(project, 'data/cache');
  mkdirSync(cache, { recursive: true });
  if (process.env.GIDS_COUNTRY_BUILD_LOCKED !== '1') {
    const result = spawnSync('flock', ['-x', resolve(cache, 'country-template-build.lock'), process.execPath, fileURLToPath(import.meta.url), '--baseline', resolve(args.baseline), '--output', resolve(args.output), '--countries', args.countries || 'all', ...(args['baseline-digest'] ? ['--baseline-digest', args['baseline-digest']] : []), ...(args['baseline-release-sha256'] ? ['--baseline-release-sha256', args['baseline-release-sha256']] : [])],
      { cwd: site, env: { ...process.env, GIDS_COUNTRY_BUILD_LOCKED: '1' }, stdio: 'inherit' });
    if (result.error) throw result.error;
    process.exit(result.status ?? 1);
  }
  const work = resolve(cache, 'country-template-work');
  try {
    const receipt = buildCountryTemplate({ site, work, baseline: resolve(args.baseline), destination: resolve(args.output), countries: (args.countries || 'all').split(','), expectedBaselineDigest: args['baseline-digest'], expectedBaselineReleaseSha256: args['baseline-release-sha256'] });
    const { countries, pages, ...summary } = receipt;
    console.log(JSON.stringify({ event: 'country_template_candidate', ...summary, country_count: countries.length, receipt: `${resolve(args.output)}.receipt.json` }));
  } finally { rmSync(work, { recursive: true, force: true }); }
}
