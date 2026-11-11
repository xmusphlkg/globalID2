import { expect, test } from '@playwright/test';
import { readFile, stat } from 'node:fs/promises';
import { resolve, sep, extname } from 'node:path';

test('the deployed library and its complete catalogue use Papers while shared navigation uses the main site', async ({ page, context }) => {
  test.skip(process.env.GIDS_VERIFY_PAPERS_BUNDLE !== '1', 'Requires the built and split production bundle.');
  test.setTimeout(60_000);
  const mainOrigin = 'https://globalinfectiousdisease.com';
  const papersOrigin = 'https://papers.globalinfectiousdisease.com';
  const mainRoot = resolve('dist');
  const papersRoot = resolve('../exports/papers-site');
  const requests: string[] = [];
  const missing: string[] = [];
  const mime: Record<string, string> = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp', '.woff2': 'font/woff2', '.xml': 'application/xml' };
  await context.route(/^https:\/\/(?:(?:papers|reports)\.)?globalinfectiousdisease\.com\//, async route => {
    const url = new URL(route.request().url());
    const root = url.origin === papersOrigin ? papersRoot : mainRoot;
    let file = resolve(root, `.${decodeURIComponent(url.pathname)}`);
    if (!file.startsWith(root + sep)) return route.fulfill({ status: 403 });
    try {
      if ((await stat(file)).isDirectory()) file = resolve(file, 'index.html');
      const body = await readFile(file);
      await route.fulfill({ status: 200, contentType: mime[extname(file)] || 'application/octet-stream', body });
    } catch {
      if (!url.pathname.startsWith('/site-data/') && !url.pathname.endsWith('/favicon.ico')) missing.push(url.href);
      await route.fulfill({ status: 404 });
    }
  });
  page.on('request', request => requests.push(request.url()));
  await page.goto(`${mainOrigin}/zh/diseases/influenza/`);
  const link = page.locator('.disease-research-panel a').filter({ hasText: /查看全部.*篇文献/ });
  await expect(link).toHaveAttribute('href', `${papersOrigin}/zh/research/diseases/influenza/`);
  await link.click();
  await expect(page).toHaveURL(`${papersOrigin}/zh/research/diseases/influenza/`);
  await expect(page.locator('[data-literature-explorer]')).toHaveAttribute('data-catalogue-ready', 'true');
  await expect(page.locator('[data-literature-record]').first()).toBeVisible();
  await expect(page.locator('[data-literature-record]')).toHaveCount(24);
  await expect(page.locator('.literature-hero-links a').first()).toHaveAttribute('href', `${mainOrigin}/zh/diseases/influenza/`);
  expect(requests.some(url => url === `${papersOrigin}/research/catalogue/diseases/d038.json`)).toBe(true);
  expect(requests.some(url => url === `${mainOrigin}/research/catalogue/diseases/d038.json`)).toBe(false);
  expect(requests.some(url => new RegExp(`^${papersOrigin}/research/catalogue/diseases/d038/\\d+-[a-f0-9]+\\.json$`).test(url))).toBe(true);
  expect(missing).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  await context.unrouteAll({ behavior: 'wait' });
});
