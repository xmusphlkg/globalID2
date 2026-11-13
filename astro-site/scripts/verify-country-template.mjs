// Browser verification uses an entirely local candidate and frozen JSON fixtures.
import { chromium } from 'playwright';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { extname, resolve } from 'node:path';
import assert from 'node:assert/strict';

const site = resolve(import.meta.dirname, '..'), root = resolve(site, '..');
const candidate = resolve(process.argv[2] || resolve(root, 'exports/country-template-stage-all'));
const evidence = resolve(process.argv[3] || `${candidate}.browser`);
mkdirSync(evidence, { recursive: true });
const origin = 'https://country-template.invalid';
const mime = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml',
  '.woff2': 'font/woff2', '.woff': 'font/woff', '.png': 'image/png', '.webp': 'image/webp', '.jpg': 'image/jpeg',
  '.json': 'application/json', '.ico': 'image/x-icon' };
const fixtures = Object.fromEntries(['surveillance', 'situation'].map(channel => {
  const directory = resolve(root, 'exports', `${channel}-data`);
  const body = readFileSync(resolve(directory, 'current.json'));
  const manifest = JSON.parse(body), digest = createHash('sha256').update(body).digest('hex');
  const names = channel === 'surveillance' ? ['home-summary.json', 'home-influenza.json', 'site-status.json'] : ['summary.json'];
  const bootstrap = JSON.stringify({ schema_version: 1, release_id: manifest.release_id,
    files: Object.fromEntries(names.filter(name => manifest.files[name]).map(name => [name, manifest.files[name]])),
    manifest: { url: `manifests/${digest}.json`, sha256: digest, bytes: body.length } });
  return [channel, { directory, manifest, digest, body, bootstrap }];
}));
function localFile(pathname) {
  let filename = resolve(candidate, '.' + decodeURIComponent(pathname));
  if (!filename.startsWith(candidate + '/')) return null;
  if (existsSync(filename) && statSync(filename).isDirectory()) filename = resolve(filename, 'index.html');
  return existsSync(filename) && statSync(filename).isFile() ? filename : null;
}
const browser = await chromium.launch({ headless: true });
const results = [];
try {
  for (const country of ['jp', 'cn']) for (const locale of ['en', 'zh', 'fr']) for (const width of [390, 1280]) {
    if (process.env.GIDS_COUNTRY_BROWSER_CASE && process.env.GIDS_COUNTRY_BROWSER_CASE !== `${country}-${locale}-${width}`) continue;
    const prefix = locale === 'en' ? '' : `/${locale}`;
    const pathname = `${prefix}/countries/${country}/`;
    const context = await browser.newContext({ viewport: { width, height: 900 } });
    const missing = [], blockedExternal = new Set(), pageErrors = [], fixtureRequests = new Set(), resourceRequests = new Set();
    try {
      await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== origin) { blockedExternal.add(url.origin); return route.abort(); }
        const situation = url.pathname.startsWith('/site-data/situation/');
        const channel = situation ? 'situation' : 'surveillance';
        const dataPrefix = situation ? '/site-data/situation/' : '/site-data/';
        const relative = url.pathname.startsWith(dataPrefix) ? url.pathname.slice(dataPrefix.length) : null;
        const fixture = fixtures[channel];
        if (relative === 'bootstrap.json' || relative === 'current.json' || relative === `manifests/${fixture.digest}.json`) {
          fixtureRequests.add(url.pathname);
          return route.fulfill({ contentType: 'application/json', body: relative === 'bootstrap.json' ? fixture.bootstrap : fixture.body });
        }
        if (relative && /^objects\/[a-f0-9]{64}\.json$/.test(relative)) {
          fixtureRequests.add(url.pathname);
          const object = resolve(fixture.directory, relative);
          if (existsSync(object)) return route.fulfill({ contentType: 'application/json', body: readFileSync(object) });
        }
        const filename = localFile(url.pathname);
        if (filename) {
          resourceRequests.add(url.pathname);
          return route.fulfill({ body: readFileSync(filename), contentType: mime[extname(filename)] || 'application/octet-stream' });
        }
        missing.push(url.pathname);
        return route.fulfill({ status: 404, contentType: 'text/plain', body: 'Missing local candidate resource' });
      });
      const page = await context.newPage();
      page.on('pageerror', error => pageErrors.push(error.stack || error.message));
      await page.goto(origin + pathname, { waitUntil: 'networkidle' });
      await page.locator('h1').first().waitFor({ state: 'visible' });
      const pageHeight = await page.evaluate(() => document.documentElement.scrollHeight);
      for (let offset = 0; offset < Math.min(pageHeight, 16000); offset += 700) {
        await page.evaluate(y => scrollTo(0, y), offset);
        await page.waitForTimeout(120);
      }
      await page.waitForLoadState('networkidle');
      await page.evaluate(() => scrollTo(0, 0));
      const observed = await page.evaluate(() => ({ title: document.title, language: document.documentElement.lang,
        h1: document.querySelector('h1')?.textContent?.trim(),
        canonical: document.querySelector('link[rel="canonical"]')?.getAttribute('href'),
        viewportWidth: innerWidth, documentWidth: document.documentElement.scrollWidth,
        navigation: [...document.querySelectorAll('header nav a[href]')].map(link => link.getAttribute('href')),
        languages: [...document.querySelectorAll('.site-language-option')].map(link => ({ locale: link.getAttribute('hreflang'), href: link.getAttribute('href') })),
        textLength: document.body.innerText.length }));
      assert.ok(observed.language.startsWith(locale), `Wrong language at ${pathname}`);
      assert.ok(observed.h1 && observed.textLength > 500, `Unreadable country body at ${pathname}`);
      assert.ok(observed.canonical?.endsWith(pathname), `Wrong canonical at ${pathname}`);
      assert.ok(observed.documentWidth <= width + 2, `Horizontal overflow at ${pathname}/${width}`);
      for (const language of observed.languages) {
        const expectedPrefix = language.locale.startsWith('zh') ? '/zh' : language.locale.startsWith('fr') ? '/fr' : '';
        assert.equal(new URL(language.href, origin).pathname, `${expectedPrefix}/countries/${country}/`);
        assert.ok(localFile(new URL(language.href, origin).pathname), 'Language target missing from candidate');
      }
      const localNavigation = [...new Set(observed.navigation.filter(href => href?.startsWith('/')))];
      for (const href of localNavigation) assert.ok(localFile(new URL(href, origin).pathname), `Navigation target missing: ${href}`);
      const screenshot = `${country}-${locale}-${width}.png`;
      await page.screenshot({ path: resolve(evidence, screenshot), fullPage: false });
      const switchLocale = locale === 'fr' ? 'en' : 'fr';
      if (width < 1024) {
        await page.locator('#mobile-menu-toggle').click();
        assert.equal(await page.locator('#mobile-menu-toggle').getAttribute('aria-expanded'), 'true');
        await page.locator(`.site-mobile-language-options a[hreflang^="${switchLocale}"]`).click();
      } else {
        await page.locator('.site-language-selector > summary').click();
        await page.locator(`.site-language-option[hreflang^="${switchLocale}"]`).click();
      }
      await page.waitForURL(url => url.pathname === `${switchLocale === 'en' ? '' : '/' + switchLocale}/countries/${country}/`);
      await page.waitForLoadState('networkidle');
      assert.deepEqual(missing, [], `Missing candidate resources: ${missing.join(', ')}`);
      assert.deepEqual(pageErrors, [], `Browser JavaScript errors: ${pageErrors.join(', ')}`);
      results.push({ pathname, width, passed: true, ...observed, screenshot,
        language_switch_target: new URL(page.url()).pathname, local_navigation_checked: localNavigation.length,
        scrolled_height: pageHeight, static_resource_requests: resourceRequests.size, json_fixture_requests: fixtureRequests.size,
        blocked_external_origins: [...blockedExternal], missing_resources: missing, page_errors: pageErrors });
    } catch (error) {
      results.push({ pathname, width, passed: false, error: String(error), missing_resources: missing,
        page_errors: pageErrors, blocked_external_origins: [...blockedExternal] });
      throw error;
    } finally { await context.close(); }
  }
} finally {
  await browser.close();
  writeFileSync(resolve(evidence, 'verification.json'), JSON.stringify({ observed_at: new Date().toISOString(),
    candidate, scope: 'Local static candidate with frozen surveillance/Situation JSON fixtures; external network blocked; no production deployment',
    cases: results.length, passed: results.length === 12 && results.every(result => result.passed),
    fixture_versions: Object.fromEntries(Object.entries(fixtures).map(([key, value]) => [key, { release_id: value.manifest.release_id, manifest_sha256: value.digest }])),
    results }, null, 2));
}
console.log(JSON.stringify({ passed: true, cases: results.length, evidence }));
