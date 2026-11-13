// Test the theme contract against retained public HTML without rebuilding it.
// This is a local browser fixture check, not a deployment/performance receipt.
import { chromium } from 'playwright';
import { readFileSync, existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { resolve, extname } from 'node:path';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';
import { buildTheme } from './build-theme.mjs';

const site = resolve(import.meta.dirname, '..');
const dist = resolve(process.argv[2] ?? resolve(site, 'dist'));
const source = JSON.parse(readFileSync(resolve(site, '../shared/site-theme-v1.json'), 'utf8'));
const updated = structuredClone(source);
updated.root['--color-teal'] = '21 129 119';
updated.mobile['--page-gutter'] = '1.25rem';
const themes = { baseline: buildTheme(source), updated: buildTheme(updated), unavailable: null };
const results = [];
const mime = { '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml', '.woff2': 'font/woff2',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.json': 'application/json' };
const browser = await chromium.launch({ headless: true });
try {
  for (const pathname of ['/', '/zh/', '/fr/', '/countries/cn/', '/zh/countries/cn/', '/fr/countries/cn/']) {
    const filename = resolve(dist, '.' + pathname, 'index.html');
    const original = readFileSync(filename, 'utf8');
    const html = original.replace('</head>', '<link rel="stylesheet" href="/theme/compat/v1/theme.css"></head>');
    const htmlHash = createHash('sha256').update(html).digest('hex');
    for (const width of [390, 1280]) {
      for (const [mode, theme] of Object.entries(themes)) {
        const context = await browser.newContext({ viewport: { width, height: 900 }, javaScriptEnabled: false });
        try {
          await context.route('**/*', async route => {
            const url = new URL(route.request().url());
            if (url.origin !== 'https://theme-pilot.invalid') return route.abort();
            if (url.pathname === pathname) return route.fulfill({ body: html, contentType: 'text/html' });
            if (url.pathname.startsWith('/theme/')) {
              if (!theme) return route.fulfill({ status: 503, body: '' });
              const index = theme.manifest.objects.findIndex(object => '/' + object.key === url.pathname);
              return route.fulfill({ status: index < 0 ? 404 : 200,
                body: index === 0 ? theme.css : index === 1 ? theme.entry : '', contentType: 'text/css' });
            }
            const local = resolve(dist, '.' + url.pathname);
            if (!local.startsWith(dist + '/') || !existsSync(local)) return route.fulfill({ status: 404, body: '' });
            return route.fulfill({ body: readFileSync(local), contentType: mime[extname(local)] ?? 'application/octet-stream' });
          });
          const page = await context.newPage();
          await page.goto('https://theme-pilot.invalid' + pathname, { waitUntil: 'load' });
          const values = await page.evaluate(() => {
            document.documentElement.dataset.theme = 'light';
            const style = getComputedStyle(document.documentElement);
            return { teal: style.getPropertyValue('--color-teal').trim(),
              gutter: style.getPropertyValue('--page-gutter').trim(), text: document.body.innerText.length };
          });
          // The failure case must agree with the bundled fallback, which can
          // intentionally differ from a future updated theme token source.
          const expectedTeal = mode === 'unavailable' ? '15 118 110' : mode === 'updated' ? '21 129 119' : source.root['--color-teal'];
          const expectedGutter = mode === 'unavailable' ? (width === 390 ? '1rem' : '2rem')
            : width === 390 ? (mode === 'updated' ? '1.25rem' : source.mobile['--page-gutter']) : source.root['--page-gutter'];
          assert.equal(values.teal, expectedTeal);
          assert.equal(values.gutter, expectedGutter);
          assert.ok(values.text > 200);
          results.push({ pathname, width, mode, html_sha256: htmlHash, ...values });
        } finally { await context.close(); }
      }
    }
    assert.equal(readFileSync(filename, 'utf8'), original);
  }
} finally { await browser.close(); }
const output = resolve(site, '../exports/theme-candidate');
mkdirSync(output, { recursive: true });
const report = { observed_at: new Date().toISOString(),
  scope: 'Existing static HTML fixtures with stable theme link injected; no production deployment or new Astro page assembly',
  cases: results.length, passed: true, results };
writeFileSync(resolve(output, 'browser-verification.json'), JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify({ passed: true, cases: results.length, output }));
