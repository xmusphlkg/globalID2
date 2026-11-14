// Read-only public release acceptance; see docs/publishing/PUBLIC_RELEASE_ACCEPTANCE.md.
// Optional EXPECTED_* env values bind this audit to a newly completed release.
import { chromium, request, expect } from '@playwright/test';
import { createHash } from 'node:crypto';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readChangelog } from '../src/lib/changelog.ts';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const out = resolve(process.env.LIVE_AUDIT_OUTPUT || `${root}/exports/live-acceptance/${new Date().toISOString().replace(/[:.]/g, '-')}`);
const main = process.env.LIVE_MAIN_ORIGIN || 'https://globalinfectiousdisease.com';
const papers = process.env.LIVE_PAPERS_ORIGIN || 'https://papers.globalinfectiousdisease.com';
const slug = process.env.LIVE_ARTICLE_SLUG || '10-1001-jama-1997-03540480040036-57010812';
const api = await request.newContext({ timeout: 45000, userAgent: 'GIDS-ReadOnly-Acceptance/0.19.2' });
const report = { startedAt: new Date().toISOString(), origins: { main, papers }, readonly: true, checks: [], pages: [], endpoints: [] };
const hash = value => createHash('sha256').update(value).digest('hex');
const check = (name, ok, detail) => report.checks.push({ name, ok: !!ok, detail });
const endpointPaths = { release: '/release.json', surveillance: '/site-data/current.json', bootstrap: '/site-data/bootstrap.json', situation: '/site-data/situation/current.json', research: '/site-data/research-publication/current.json' };
await mkdir(out, { recursive: true });
async function get(url, name, fresh = false) {
  const response = await api.get(url, { headers: fresh ? { 'Cache-Control': 'no-cache' } : {} });
  const body = await response.body();
  const headers = response.headers();
  const entry = { name, url, finalUrl: response.url(), status: response.status(), bytes: body.length, sha256: hash(body), headers: Object.fromEntries(Object.entries(headers).filter(([key]) => /^(cache-control|etag|last-modified|age|cf-cache-status|content-type|location)$/.test(key) || key.startsWith('x-gids-'))) };
  report.endpoints.push(entry);
  return { ...entry, body, text: body.toString('utf8'), json: () => JSON.parse(body.toString('utf8')) };
}
async function checked(name, action) {
  try { await action(); } catch (error) { check(name, false, String(error)); }
}
const identities = {};
for (const [name, path] of Object.entries(endpointPaths)) await checked(`read ${name}`, async () => {
  const result = await get(main + path, name, true);
  check(`${name} reachable`, result.status === 200, result.status);
  identities[name] = result.json();
  check(`${name} mutable cache policy`, /no-store|max-age=0/.test(result.headers['cache-control'] || ''), result.headers);
  await writeFile(`${out}/${name}.json`, result.body);
});
const expected = {
  release: process.env.EXPECTED_RELEASE_ID,
  surveillance: process.env.EXPECTED_SURVEILLANCE_RELEASE,
  research: process.env.EXPECTED_RESEARCH_RELEASE,
};
for (const [name, value] of Object.entries(expected)) if (value) check(`${name} matches expected release`, identities[name]?.release_id === value, { expected: value, actual: identities[name]?.release_id });
if (process.env.EXPECTED_SOURCE_COMMIT) check('source commit matches expected', identities.release?.source_commit === process.env.EXPECTED_SOURCE_COMMIT, identities.release?.source_commit);
if (process.env.EXPECTED_MIN_SURVEILLANCE_DATE) check('surveillance publication date advanced', identities.surveillance?.generated_at >= process.env.EXPECTED_MIN_SURVEILLANCE_DATE, identities.surveillance?.generated_at);
if (process.env.LIVE_BASELINE_REPORT) {
  const before = JSON.parse(await readFile(process.env.LIVE_BASELINE_REPORT, 'utf8'));
  report.baselineComparison = Object.fromEntries(['release', 'surveillance', 'research', 'situation'].map(name => [name, { before: before.identities?.[name]?.release_id, after: identities[name]?.release_id, changed: before.identities?.[name]?.release_id !== identities[name]?.release_id }]));
}
check('bootstrap is same surveillance release', identities.bootstrap?.release_id === identities.surveillance?.release_id, { bootstrap: identities.bootstrap?.release_id, surveillance: identities.surveillance?.release_id });
await checked('public changelog dependency', async () => {
  const response = await get(main + '/site-data/updates/changelog.json', 'public-changelog', true);
  check('public changelog available on main host', response.status === 200, response.status);
  const releases = readChangelog(response.json());
  report.changelog = { count: releases.length, latestVersion: releases[0]?.version, sha256: response.sha256 };
  if (process.env.EXPECTED_APP_VERSION) check('public changelog latest version', releases[0]?.version === process.env.EXPECTED_APP_VERSION, report.changelog);
});
await checked('public data-update history', async () => {
  const index = (await get(main + '/site-data/updates/latest.json', 'public-update-index', true)).json();
  const month = index.months?.[0];
  check('public update month index', index.schema_version === 1 && /^\d{4}-\d{2}$/.test(month), index);
  if (month) {
    const response = await get(`${main}/site-data/updates/${month}.json`, 'public-update-month', true);
    const archive = response.json();
    check('public update month available', response.status === 200 && archive.month === month && Array.isArray(archive.events), { month, status: response.status, count: archive.events?.length });
  }
});
if (identities.surveillance && identities.bootstrap) check('bootstrap manifest digest', identities.bootstrap.manifest.sha256 === hash(await readFile(`${out}/surveillance.json`)), identities.bootstrap.manifest);
for (const [channel, names, prefix] of [
  ['surveillance', ['meta.json', 'site-status.json', 'countries/jp-summary.json', 'diseases/d038-summary.json'], '/site-data/'],
  ['research', ['index.json', 'catalogue.json'], '/site-data/research-publication/'],
]) for (const name of names) await checked(`${channel} ${name} integrity`, async () => {
  const entry = identities[channel]?.files?.[name];
  if (!entry) return check(`${channel} ${name} published`, false, 'missing manifest entry');
  const result = await get(new URL(entry.url, main + prefix).href, `${channel}:${name}`);
  check(`${channel} ${name} integrity`, result.status === 200 && result.sha256 === entry.sha256 && result.bytes === entry.bytes, { expected: entry, actual: { status: result.status, sha256: result.sha256, bytes: result.bytes } });
  const data = result.json();
  report[`${channel}:${name}`] = Object.fromEntries(Object.entries(data).filter(([key]) => ['generated_at', 'total_countries', 'total_diseases', 'date_range', 'total', 'article_count', 'published_count', 'count', 'stats'].includes(key)));
});
const generation = identities.research?.papers?.papers_generation_sha256;
if (generation) await checked('Papers catalogue', async () => {
  const binding = identities.research.papers;
  const result = await get(binding.papers_manifest_url, 'papers-catalogue');
  check('Papers catalogue digest', result.status === 200 && result.sha256 === binding.papers_manifest_sha256, { expected: binding.papers_manifest_sha256, actual: result.sha256 });
  const catalogue = result.json();
  check('Papers catalogue generation', catalogue.generation_sha256 === generation && catalogue.schema_version === 2, { schema: catalogue.schema_version, generation: catalogue.generation_sha256 });
});
const metaGeneration = html => html.match(/<meta name="gids-research-generation" content="([a-f0-9]{64})"/)?.[1];
const attr = (html, name) => html.match(new RegExp(`${name}="([^"]+)"`))?.[1];
for (const path of ['/', '/countries/jp/']) await checked(`theme pilot ${path}`, async () => {
  const page = await get(main + path, `theme-pilot:${path}`, true);
  const theme = page.text.match(/href="([^" ]*\/theme\/compat\/v1\/theme\.css)"/)?.[1];
  check(`theme pilot reference ${path}`, page.status === 200 && !!theme, theme);
  if (!theme) return;
  const entry = await get(new URL(theme, main).href, `theme-entry:${path}`, true);
  const relative = entry.text.match(/^@import url\("([^"\n]+)"\);\n$/)?.[1];
  check(`theme entry cache ${path}`, /(?:^|[, ])max-age=0(?:,|$)/.test(entry.headers['cache-control'] || ''), entry.headers['cache-control']);
  if (relative) {
    const css = await get(new URL(relative, entry.url).href, `theme-css:${path}`);
    check(`theme CSS integrity ${path}`, css.status === 200 && css.url.endsWith(`/${css.sha256}.css`) && /immutable/.test(css.headers['cache-control']), { url: css.url, sha256: css.sha256 });
  } else check(`theme entry format ${path}`, false, entry.text);
});
for (const locale of ['', '/zh', '/fr']) for (const origin of [main, papers]) await checked(`stable article ${origin}${locale}`, async () => {
  const url = `${origin}${locale}/research/articles/${slug}/`;
  const normal = await get(url, `article:${locale || 'en'}:${origin}`);
  const fresh = await get(url + '?gids_readonly_probe=1', `article-fresh:${locale || 'en'}:${origin}`, true);
  const canonical = normal.text.match(/<link rel="canonical" href="([^"]+)"/)?.[1];
  check(`article stable contract ${origin}${locale}`, normal.status === 200 && normal.headers['x-gids-paper-mode'] === 'projection-v3' && normal.headers['cache-control'] === 'no-store' && normal.headers['x-gids-research-release'] === identities.research?.release_id && metaGeneration(normal.text) === generation && canonical === `${papers}${locale}/research/articles/${slug}/`, { headers: normal.headers, generation: metaGeneration(normal.text), canonical });
  check(`article cache parity ${origin}${locale}`, fresh.status === normal.status && metaGeneration(fresh.text) === generation && fresh.headers['x-gids-paper-content-sha256'] === normal.headers['x-gids-paper-content-sha256'] && fresh.headers['x-gids-paper-template-sha256'] === normal.headers['x-gids-paper-template-sha256'], fresh.headers);
  check(`article content marker ${origin}${locale}`, attr(normal.text, 'data-research-article-sha256') === normal.headers['x-gids-paper-content-sha256'], attr(normal.text, 'data-research-article-sha256'));
});
for (const origin of [main, papers]) for (const path of [
  `/research-publication/${generation}/research/`,
  `/research-publication/${generation}/research/articles/${slug}/`,
  '/site-data/research/index.json', '/site-data/research/catalogue.json',
]) await checked(`retired route ${origin}${path}`, async () => {
  const result = await get(origin + path, 'retired-route');
  const status = path.startsWith('/research-publication/') ? 410 : 404;
  check(`retired route ${origin}${path}`, result.status === status, { expected: status, actual: result.status, cache: result.headers['cache-control'] });
});
let browser;
if (process.env.LIVE_API_ONLY !== '1') {
  browser = await chromium.launch({ headless: true });
  const pages = [
    ...[360, 1280].flatMap(width => [
      { url: `${main}/zh/data-updates/`, width, kind: 'updates' },
      { url: `${main}/research/`, width, kind: 'research' },
      { url: `${papers}/zh/research/articles/${slug}/`, width, kind: 'article' },
    ]),
    { url: `${papers}/fr/research/diseases/influenza/`, width: 1280, kind: 'collection' },
    { url: `${main}/diseases/influenza/`, width: 1280, kind: 'disease' },
  ];
  for (const spec of pages) await checked(`browser ${spec.url} ${spec.width}`, async () => {
    const page = await browser.newPage({ viewport: { width: spec.width, height: 960 }, reducedMotion: 'reduce' });
    const errors = [], bad = [], dataReads = [];
    await page.route('**/*', route => ['GET', 'HEAD', 'OPTIONS'].includes(route.request().method()) ? route.continue() : route.abort());
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => {
      if (response.status() >= 400 && !/stats\.|cloudflareinsights/.test(response.url())) bad.push({ url: response.url(), status: response.status() });
      if (/site-data\/|research-publication\//.test(response.url())) dataReads.push({ url: response.url(), status: response.status() });
    });
    const navigation = await page.goto(spec.url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.locator('h1').first().waitFor({ state: 'visible' });
    if (spec.kind === 'updates') await page.locator('.updates-trend-chart').waitFor({ state: 'visible', timeout: 30000 });
    if (spec.kind === 'disease') await page.locator('[data-surveillance-version]').first().waitFor({ state: 'attached', timeout: 30000 });
    const footer = page.locator('footer.site-footer');
    if (await footer.count()) await footer.scrollIntoViewIfNeeded();
    await expect.poll(() => page.locator('astro-island[client="load"][ssr]').count(), { timeout: 30000 }).toBe(0);
    const observed = await page.evaluate(() => ({
      title: document.title, heading: document.querySelector('h1')?.textContent,
      generation: document.querySelector('meta[name="gids-research-generation"]')?.content,
      canonical: document.querySelector('link[rel="canonical"]')?.href,
      headerCount: document.querySelectorAll('header.site-header').length,
      footerCount: document.querySelectorAll('footer.site-footer').length,
      themeLinks: [...document.querySelectorAll('link[rel="stylesheet"]')].map(x => x.href).filter(x => /theme/.test(x)),
      overflow: document.documentElement.scrollWidth > innerWidth,
      versions: [...document.querySelectorAll('[data-surveillance-version]')].map(x => x.getAttribute('data-surveillance-version')),
      footerText: document.querySelector('footer.site-footer')?.textContent,
    }));
    check(`browser layout ${spec.kind} ${spec.width}`, navigation.ok() && !observed.overflow && observed.headerCount === 1 && observed.footerCount === 1, observed);
    check(`browser runtime ${spec.kind} ${spec.width}`, !errors.length && !bad.length, { errors, bad });
    if (['research', 'article', 'collection'].includes(spec.kind)) check(`browser research generation ${spec.kind} ${spec.width}`, observed.generation === generation, observed.generation);
    if (spec.kind === 'disease') check('browser surveillance generation', observed.versions.length > 0 && observed.versions.every(value => value === identities.surveillance?.release_id), observed.versions);
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: `${out}/${spec.kind}-${spec.width}.png`, fullPage: true });
    report.pages.push({ ...spec, ...observed, errors, bad, dataReads });
    await page.close();
  });
}
for (const name of ['release', 'surveillance', 'research', 'situation']) await checked(`${name} stable during audit`, async () => {
  const end = (await get(main + endpointPaths[name], `${name}:end`, true)).json();
  check(`${name} stable during audit`, end.release_id === identities[name]?.release_id, { before: identities[name]?.release_id, after: end.release_id });
});
report.identities = identities;
report.finishedAt = new Date().toISOString();
report.summary = { passed: report.checks.filter(x => x.ok).length, failed: report.checks.filter(x => !x.ok).length };
await writeFile(`${out}/report.json`, JSON.stringify(report, null, 2));
await browser?.close(); await api.dispose();
console.log(JSON.stringify({ output: out, ...report.summary, failures: report.checks.filter(x => !x.ok) }, null, 2));
process.exitCode = report.summary.failed ? 1 : 0;
