// Build the reports routes in isolation without replacing the main site's dist.
import { cpSync, existsSync, mkdirSync, readdirSync, rmSync, symlinkSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const site = resolve(import.meta.dirname, '..');
const project = resolve(site, '..');
const base = resolve(project, 'exports');
mkdirSync(base, { recursive: true });
const cache = resolve(project, 'data/cache');
mkdirSync(cache, { recursive: true });
if (process.env.GIDS_RESEARCH_BUILD_LOCKED !== '1') {
  const result = spawnSync('flock', ['-x', resolve(cache, 'research-public-artifacts.lock'), process.execPath, fileURLToPath(import.meta.url)], {
    cwd: site, env: { ...process.env, GIDS_RESEARCH_BUILD_LOCKED: '1' }, stdio: 'inherit',
  });
  if (result.error) throw result.error;
  process.exit(result.status ?? 1);
}
// Astro includes component paths when producing island IDs. A stable source
// path prevents unchanged report HTML from changing on every build.
const work = resolve(cache, 'reports-build-work');
rmSync(work, { recursive: true, force: true });
const source = resolve(work, 'src');
const pages = resolve(source, 'pages');
const publicDir = resolve(work, 'public');
const reportOutput = process.env.GIDS_REPORTS_BUILD_OUTPUT;
if (!reportOutput) throw new Error('GIDS_REPORTS_BUILD_OUTPUT must name an isolated output directory');
const destination = resolve(reportOutput);
if (dirname(destination) !== base || !destination.startsWith(`${base}/reports-stage-`)) {
  throw new Error('Reports build output must be a reports-stage-* directory directly under exports');
}

try {
  mkdirSync(pages, { recursive: true });
  mkdirSync(publicDir, { recursive: true });
  for (const entry of readdirSync(resolve(site, 'src'), { withFileTypes: true })) {
    if (entry.name !== 'pages') symlinkSync(resolve(site, 'src', entry.name), resolve(source, entry.name), entry.isDirectory() ? 'dir' : 'file');
  }
  for (const path of [
    'reports', 'reports-host', 'countries/[country]/reports',
    'zh/reports', 'zh/countries/[country]/reports',
    'fr/reports', 'fr/countries/[country]/reports',
  ]) {
    const destination = resolve(pages, path);
    mkdirSync(dirname(destination), { recursive: true });
    cpSync(resolve(site, 'src/pages', path), destination, { recursive: true });
  }
  // Shared public files are small; the large surveillance/research JSON tree
  // stays on the main host and must not be copied into this isolated build.
  for (const entry of readdirSync(resolve(site, 'public'), { withFileTypes: true })) {
    if (entry.name === 'site-data' || entry.name === '_headers' || entry.name === '_redirects') continue;
    cpSync(resolve(site, 'public', entry.name), resolve(publicDir, entry.name), { recursive: true });
  }
  const reportData = resolve(site, 'public/site-data/reports');
  if (existsSync(reportData)) {
    mkdirSync(resolve(publicDir, 'site-data'), { recursive: true });
    cpSync(reportData, resolve(publicDir, 'site-data/reports'), { recursive: true });
  }
  const result = spawnSync(resolve(site, 'node_modules/.bin/astro'), ['build'], {
    cwd: site,
    env: { ...process.env, GIDS_REPORTS_BUILD_DIR: work },
    stdio: 'inherit',
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exitCode = result.status ?? 1;
  else {
    rmSync(destination, { recursive: true, force: true });
    cpSync(resolve(work, 'dist'), destination, { recursive: true });
  }
} finally {
  rmSync(work, { recursive: true, force: true });
}
