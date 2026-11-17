import { expect, test, type Page } from '@playwright/test';

async function showPlaces(page: Page) {
  const toggle = page.locator('.sit-bar-links button');
  if (await toggle.getAttribute('aria-pressed') === 'false') await toggle.click();
}

async function choose(page: Page, label: string, query: string, id: string) {
  await showPlaces(page);
  await page.getByRole('combobox', { name: label, exact: true }).fill(query);
  await page.locator(`.sit-search-popup [role="option"][data-value="${id}"]`).click();
}

// These journeys use a retained, real publication through the audit server.
// Ordinary CI keeps the deterministic regression below and skips live-data work.
test.describe('French reporter using a desktop in Paris', () => {
  test.use({ locale: 'fr-FR', timezoneId: 'Europe/Paris', viewport: { width: 1280, height: 900 } });
  test('can find disease events by their French names and trace an official source', async ({ page }) => {
    test.skip(process.env.GIDS_VISITOR_REAL_DATA !== '1', 'Requires the real-publication audit server.');
    test.setTimeout(120_000);
    page.setDefaultTimeout(15_000);
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('/fr/', { waitUntil: 'domcontentloaded' });
    await page.getByRole('link', { name: 'Explorer la surveillance mondiale', exact: true }).click();
    await expect(page.locator('.sit-item').first()).toBeAttached({ timeout: 60_000 });
    await choose(page, 'Maladie', 'grippe', 'D038');
    await choose(page, 'Zone', 'France', 'FR');
    expect(new URL(page.url()).searchParams.get('disease')).toBe('D038');
    expect(new URL(page.url()).searchParams.get('country')).toBe('FR');
    if (!(await page.locator('.sit-item').count())) {
      await expect(page.locator('.sit-list')).toContainText('Aucun lieu ne correspond à ces filtres.');
    }
    await choose(page, 'Maladie', 'rougeole', 'D017');
    await expect(page.getByRole('combobox', { name: 'Maladie', exact: true })).toHaveValue('Rougeole');
    await choose(page, 'Maladie', 'dengue', 'D021');
    await expect(page.locator('.sit-item').first()).toBeVisible();
    await page.locator('.sit-item').first().click();
    const source = page.locator('.sit-current-place .sit-source-link').first();
    await expect(source).toBeVisible({ timeout: 60_000 });
    await expect(source).toHaveAttribute('href', /^https:\/\//);
    await expect(source).toHaveAttribute('target', '_blank');
    const shared = page.url();
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.locator('.sit-current-place .sit-source-link').first()).toBeAttached({ timeout: 60_000 });
    expect(page.url()).toBe(shared);
    await page.screenshot({ path: '/tmp/gids-visitors-francophone/france-source-after.png' });
    await page.locator('header').getByRole('link', { name: 'Radar de recherche', exact: true }).click();
    const article = page.locator('article .research-card-title a').first();
    await expect(article).toBeVisible();
    const title = await article.innerText();
    await article.click();
    await expect(page.locator('main h1')).toContainText(title);
    await expect(page.locator('main a[href^="https://doi.org/"]').first()).toBeVisible();
    await expect(page.locator('.evidence-citation')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(errors).toEqual([]);
  });
});

test.describe('Senegal health worker using a phone in Dakar', () => {
  test.use({ locale: 'fr-SN', timezoneId: 'Africa/Dakar', viewport: { width: 360, height: 800 }, isMobile: true, hasTouch: true });
  test('can recover an unavailable notice, distinguish missing surveillance and follow a country RSS feed', async ({ page, context }) => {
    test.skip(process.env.GIDS_VISITOR_REAL_DATA !== '1', 'Requires the real-publication audit server.');
    test.setTimeout(150_000);
    page.setDefaultTimeout(15_000);
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    const cdp = await context.newCDPSession(page);
    await page.goto('/fr/situation/', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('.sit-item').first()).toBeAttached({ timeout: 60_000 });
    // Throttle the actual user interactions after development-only module hydration.
    await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 120, downloadThroughput: 192_000, uploadThroughput: 96_000 });
    await choose(page, 'Zone', 'Senegal', 'SN');
    await expect(page.getByRole('combobox', { name: 'Zone', exact: true })).toHaveValue('Sénégal');
    await choose(page, 'Maladie', 'grippe', 'D038');
    await expect(page.locator('.sit-list')).toContainText('Aucun lieu ne correspond à ces filtres.');
    await page.locator('.sit-filter-chip[data-filter="disease"]').click();
    await expect(page.locator('.sit-item').first()).toBeVisible();
    let unavailable = true;
    await page.route('**/site-data/situation/objects/*.json', route => unavailable ? route.fulfill({ status: 503 }) : route.continue());
    await page.locator('.sit-item').first().click();
    const retry = page.locator('.sit-current-place').getByRole('button', { name: 'Réessayer', exact: true });
    await expect(retry).toBeVisible({ timeout: 60_000 });
    unavailable = false;
    await retry.click();
    await expect(page.locator('.sit-current-place .sit-source-link').first()).toBeVisible({ timeout: 60_000 });
    expect(new URL(page.url()).searchParams.get('country')).toBe('SN');
    await expect(page.locator('.sit-detail')).toContainText('GIDS ne publie pas de série de surveillance');
    await page.screenshot({ path: '/tmp/gids-visitors-francophone/senegal-recovered-after.png' });
    await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
    await page.locator('.sit-bar-links a').filter({ hasText: 'hebdomadaires' }).click();
    const week = page.locator('main a[href*="?period="]').first();
    await expect(week).toBeVisible({ timeout: 60_000 });
    await week.click();
    const outbreak = page.locator('main details').first();
    await expect(outbreak).toBeVisible({ timeout: 60_000 });
    await outbreak.locator('summary').click();
    await expect(outbreak.locator('ul a[target="_blank"]').first()).toBeVisible({ timeout: 60_000 });
    await page.goto('/fr/situation/weekly/?period=1900-W01', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('main')).toContainText('Cette période ne figure pas dans les archives actuelles.');
    await page.getByRole('link', { name: 'Toutes les semaines', exact: true }).click();
    await expect(page.locator('main a[href*="?period="]').first()).toBeVisible();
    await page.locator('footer a[href="/fr/subscribe/"]').click();
    await page.locator('#rss-country').selectOption('SN');
    const responsePromise = page.waitForResponse(response => response.url().includes('/site-data/situation/rss.xml?'));
    await page.getByRole('button', { name: 'Ouvrir le flux du pays', exact: true }).click();
    const response = await responsePromise;
    expect(response.ok()).toBe(true);
    const xml = await response.text();
    expect(xml).toContain('<language>fr</language>');
    expect(xml).toContain('<item>');
    expect(new URL(page.url()).searchParams.get('country')).toBe('SN');
    expect(errors).toEqual([]);
  });
});

test('French research search finds the labels actually shown on the records, with or without accents', async ({ page }) => {
  const article = { slug: 'french-name-regression', article_id: 'fr-search', title: 'Transmission study', journal: 'Example Journal', published_at: '2026-01-01', peer_review_status: 'peer_reviewed', detail_available: true, diseases: [{ disease_id: 'D038', slug: 'influenza', name_en: 'Influenza' }], countries: [{ code: 'SN', name_en: 'Senegal' }], topics: [{ name: 'Transmission dynamics' }] };
  await page.route('**/research/catalogue/manifest.json', route => route.fulfill({ json: { chunks: [{ path: '/research/catalogue/french-labels.json', count: 1 }] } }));
  await page.route('**/research/catalogue/french-labels.json', route => route.fulfill({ json: { articles: [article] } }));
  await page.goto('/fr/research/?d=d038', { waitUntil: 'domcontentloaded' });
  const list = page.locator('#research-list');
  await expect(list.locator('article')).toHaveCount(1);
  for (const query of ['grippe', 'influenza', 'Sénégal', 'Senegal', 'Dynamique de transmission']) {
    await page.locator('#research-search').fill(query);
    await expect(list).toHaveAttribute('aria-busy', 'false');
    await expect(list.locator('article')).toHaveCount(1);
    await expect(list).toBeVisible();
  }
  await page.locator('#research-search').fill('no-matching-record-123');
  await expect(list.locator('article')).toHaveCount(0);
  await expect(page.locator('#research-empty')).toBeVisible();
  await page.locator('#research-search').fill('');
  await expect(list.locator('article')).toHaveCount(1);
});
