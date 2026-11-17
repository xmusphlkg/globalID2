import { expect, test } from '@playwright/test';

for (const locale of ['en', 'zh', 'fr'] as const) {
  test(`geographic directory recovers without a reload and preserves filters in ${locale}`, async ({ page }) => {
    let available = false;
    await page.route('**/site-data/{bootstrap,current}.json', route => route.fulfill({ status: 404 }));
    await page.route('**/site-data/meta.json', route => available ? route.fulfill({ json: {
      countries: [{ code: 'JP', name_en: 'Japan', name_zh: '日本', name_fr: 'Japon',
        data_available: true, disease_count: 1, date_range: { start: '2025-01-01', end: '2026-11-01' } }],
    } }) : route.fulfill({ status: 503 }));
    const prefix = locale === 'en' ? '' : `/${locale}`;
    await page.goto(`${prefix}/countries/?q=JP&status=supported`);
    const directory = page.locator('.surveillance-directory');
    const retry = directory.getByRole('button', { name: { en: 'Retry', zh: '重试', fr: 'Réessayer' }[locale], exact: true });
    await expect(retry).toBeVisible();
    await expect(directory.locator('.directory-card')).toHaveCount(0);
    available = true;
    await retry.click();
    await expect(directory.locator('.directory-card')).toHaveCount(1);
    await expect(directory.locator('.directory-card')).toHaveAttribute('href', `${prefix}/countries/jp/`);
    await expect(directory.getByRole('searchbox')).toHaveValue('JP');
    await expect(page).toHaveURL(/q=JP&status=supported/);
    await expect(retry).toHaveCount(0);
  });
}

test('subdivision directory recovers after its data request fails', async ({ page }) => {
  test.setTimeout(120_000);
  let available = false;
  await page.route('**/site-data/{bootstrap,current}.json', route => route.fulfill({ status: 404 }));
  await page.route('**/site-data/meta.json', route => available ? route.fulfill({ json: {
    countries: [{ code: 'JP-01', parent_code: 'JP', location_type: 'subdivision', name_en: 'Hokkaido',
      data_available: true, disease_count: 1, date_range: { start: '2025-01-01', end: '2026-11-01' } }],
  } }) : route.fulfill({ status: 503 }));
  await page.goto('/countries/jp/#subnational-data', { timeout: 100_000 });
  const directory = page.locator('#subnational-data .surveillance-directory');
  const retry = directory.getByRole('button', { name: 'Retry', exact: true });
  await expect(retry).toBeVisible();
  available = true;
  await retry.click();
  await expect(directory.locator('.directory-card')).toHaveCount(1);
  await expect(directory.locator('.directory-card')).toContainText('Hokkaido');
  await expect(directory.locator('.directory-card')).toHaveAttribute('href', '/countries/jp-01/');
  await expect(retry).toHaveCount(0);
});
