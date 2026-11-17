import { expect, test } from '@playwright/test';

test('large geographic catalogues page cards without truncating filters or map coverage', async ({ page }) => {
  await page.route('**/site-data/{bootstrap,current}.json', route => route.fulfill({ status: 404 }));
  await page.route('**/site-data/meta.json', route => route.fulfill({ json: {
    generated_at: '2026-11-17T00:00:00Z',
    countries: Array.from({ length: 80 }, (_, id) => ({
      code: `X${String(id).padStart(3, '0')}`,
      name_en: `Pagination area ${String(id).padStart(3, '0')}`,
      disease_count: 1, data_available: true, location_type: 'country',
      date_range: { start: '2024-01-01', end: '2026-11-01' },
    })),
  } }));
  await page.goto('/countries/?q=Pagination%20area', { waitUntil: 'domcontentloaded' });
  const directory = page.locator('.surveillance-directory');
  const cards = directory.locator('.directory-card');
  await expect(cards).toHaveCount(24);
  await expect(cards.first()).toContainText('Pagination area 000');
  await expect.poll(() => page.evaluate(() => (window as any).__globalIdCountryFilterCodes?.length)).toBe(80);
  const previous = directory.getByRole('button', { name: 'Previous', exact: true });
  const next = directory.getByRole('button', { name: 'Next', exact: true });
  await expect(previous).toBeDisabled();
  await next.click();
  await expect(cards).toHaveCount(24);
  await expect(cards.first()).toContainText('Pagination area 024');
  await expect(previous).toBeEnabled();
  await expect.poll(() => page.evaluate(() => (window as any).__globalIdCountryFilterCodes?.length)).toBe(80);
  await directory.getByRole('searchbox').fill('Pagination area 079');
  await expect(cards).toHaveCount(1);
  await expect(cards.first()).toContainText('Pagination area 079');
  await expect(directory.locator('.directory-pagination')).toHaveCount(0);
  await directory.getByRole('searchbox').fill('Pagination area');
  await expect(cards).toHaveCount(24);
  await expect(cards.first()).toContainText('Pagination area 000');
});

test('country subdivision browser pages its own areas without global catalogue filters', async ({ page }) => {
  test.setTimeout(120_000);
  await page.route('**/site-data/{bootstrap,current}.json', route => route.fulfill({ status: 404 }));
  await page.route('**/site-data/meta.json', route => route.fulfill({ json: {
    generated_at: '2026-11-17T00:00:00Z',
    countries: Array.from({ length: 60 }, (_, id) => ({
      code: `JP-${String(id).padStart(3, '0')}`, parent_code: 'JP', location_type: 'subdivision',
      name_en: `Regional area ${String(id).padStart(3, '0')}`, disease_count: 1, data_available: true,
      date_range: { start: '2024-01-01', end: '2026-11-01' },
    })),
  } }));
  await page.goto('/countries/jp/#subnational-data', { waitUntil: 'domcontentloaded', timeout: 100_000 });
  const browser = page.locator('#subnational-data');
  await browser.scrollIntoViewIfNeeded();
  const cards = browser.locator('.directory-card');
  await expect(cards).toHaveCount(24);
  await expect(cards.first()).toContainText('Regional area 000');
  await expect(browser.locator('.directory-filters')).toHaveCount(0);
  await browser.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(cards).toHaveCount(24);
  await expect(cards.first()).toContainText('Regional area 024');
  await browser.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(cards).toHaveCount(12);
  await expect(cards.last()).toContainText('Regional area 059');
  await expect(browser.getByRole('button', { name: 'Next', exact: true })).toBeDisabled();
});
