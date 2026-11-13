import { chromium } from 'playwright';
import assert from 'node:assert/strict';
import { existsSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { resolve, extname } from 'node:path';

const [candidate, archive] = process.argv.slice(2).map(path => resolve(path));
if (!candidate || !archive) throw new Error('Usage: <candidate> <production browser archive>');
const origin = 'https://globalinfectiousdisease.com';
const frozen = new Map(JSON.parse(readFileSync(resolve(archive, 'responses.json'))).map(row => [row.url, row]));
const baseline = JSON.parse(readFileSync(resolve(archive, 'download-dialogs.json')));
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.woff2': 'font/woff2', '.svg': 'image/svg+xml' };
const browser = await chromium.launch({ headless: true }), results = [];
try {
  for (const expected of baseline.results) {
    const { locale, width, pathname } = expected;
    const context = await browser.newContext({ viewport: { width, height: 900 } });
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin === origin && (!url.pathname.startsWith('/site-data/') || url.pathname.startsWith('/site-data/downloads/'))) {
        let file = resolve(candidate, '.' + url.pathname);
        if (file.startsWith(candidate + '/') && existsSync(file)) {
          if (statSync(file).isDirectory()) file = resolve(file, 'index.html');
          if (existsSync(file)) return route.fulfill({ body: readFileSync(file), contentType: mime[extname(file)] || 'application/octet-stream' });
        }
      }
      const row = frozen.get(url.href);
      if (row) return route.fulfill({ body: readFileSync(resolve(archive, 'responses', row.sha256)), contentType: row.content_type,
        headers: { 'access-control-allow-origin': origin } });
      return route.abort();
    });
    try {
      const page = await context.newPage();
      await page.goto(origin + pathname, { waitUntil: 'networkidle' });
      await page.locator('[data-testid="download-archive"] [data-download-modal-trigger]').click();
      const dialog = page.locator('dialog[data-download-confirm-dialog][open]');
      await dialog.locator('a[href]').first().waitFor({ state: 'visible' });
      const links = await dialog.locator('a[href]').evaluateAll(els => els.map(e => ({ text: e.textContent,
        href: e.getAttribute('href'), download: e.getAttribute('download') })));
      assert.deepEqual(links, expected.links);
      await dialog.locator('[data-download-format-filter]').selectOption('json');
      const formats = await dialog.locator('[data-download-file]').evaluateAll(els => els.map(e => e.getAttribute('data-download-format')));
      assert.deepEqual(formats, expected.filtered_formats);
      await dialog.locator('[data-download-modal-close]').click();
      assert.equal(await page.locator('dialog[data-download-confirm-dialog][open]').count(), 0);
      results.push({ locale, width, pathname, passed: true, verified_links: links.length, verified_format: 'json' });
    } finally { await context.close(); }
  }
} finally {
  await browser.close();
  writeFileSync(`${candidate}.downloads.json`, JSON.stringify({ candidate, archive, passed: results.length === 6, results }, null, 2));
}
console.log(JSON.stringify({ passed: results.length === 6, cases: results.length }));
