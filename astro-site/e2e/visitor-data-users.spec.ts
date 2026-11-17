import { expect, test, type Page } from '@playwright/test';

test.setTimeout(60_000);

const states = ['AC', 'AL', 'AM', 'AP', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MG', 'MS', 'MT', 'PA', 'PB', 'PE', 'PI', 'PR', 'RJ', 'RN', 'RO', 'RR', 'RS', 'SC', 'SE', 'SP', 'TO'];
async function areaFixture(page: Page) {
  await page.route('**/site-data/**', route => route.fulfill({ status: 404 }));
  await page.route('**/site-data/meta.json', route => route.fulfill({ json: {
    countries: [
      ...states.map(code => ({ code: `BR-${code}`, parent_code: 'BR', location_type: 'subdivision',
        name_en: code === 'SP' ? 'São Paulo, Brazil' : `Brazil state ${code}`, data_available: true, disease_count: 1 })),
      ...Array.from({ length: 60 }, (_, index) => ({ code: `V${index}`, name_en: `Visitor area ${String(index).padStart(2, '0')}`,
        location_type: 'country', data_available: true, disease_count: 1 })),
    ],
  } }));
}

test('Brazil visitor returns to the same subdivision page after opening a state, refreshing and changing language', async ({ page }) => {
  await areaFixture(page);
  await page.goto('/countries/br/?audit=kept#subnational-data', { waitUntil: 'domcontentloaded' });
  const directory = page.locator('#subnational-data');
  await directory.scrollIntoViewIfNeeded();
  await directory.getByRole('button', { name: 'Next', exact: true }).click();
  await expect(page).toHaveURL(/areas_page=2/);
  await expect(directory.locator('.directory-card')).toHaveCount(3);
  await directory.locator('a[href="/countries/br-sp/"]').click();
  await expect(page).toHaveURL(/\/countries\/br-sp\//);
  await page.goBack({ waitUntil: 'domcontentloaded' });
  await directory.scrollIntoViewIfNeeded();
  await expect(directory.locator('.directory-card')).toHaveCount(3);
  await expect(directory.locator('a[href="/countries/br-sp/"]')).toBeVisible();
  await page.reload({ waitUntil: 'domcontentloaded' });
  await directory.scrollIntoViewIfNeeded();
  await expect(directory.locator('.directory-card')).toHaveCount(3);
  await page.locator('.site-language-selector summary').click();
  await page.locator('.site-language-selector a[hreflang="fr"]').click();
  await directory.scrollIntoViewIfNeeded();
  await expect(directory.locator('.directory-card')).toHaveCount(3);
  expect(new URL(page.url()).searchParams.get('audit')).toBe('kept');
  expect(new URL(page.url()).searchParams.get('areas_page')).toBe('2');
  await expect(directory.locator('.directory-pagination')).toContainText('Page 2 sur 2');
});

test('shared geographic catalogue pages survive reload and reset when the visitor changes a filter', async ({ page }) => {
  await areaFixture(page);
  await page.goto('/countries/?q=Visitor%20area&areas_page=2', { waitUntil: 'domcontentloaded' });
  const directory = page.locator('.surveillance-directory');
  await expect(directory.locator('.directory-card').first()).toContainText('Visitor area 24');
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(directory.locator('.directory-card').first()).toContainText('Visitor area 24');
  await directory.getByRole('searchbox').fill('Visitor area 59');
  await expect(directory.locator('.directory-card')).toHaveCount(1);
  expect(new URL(page.url()).searchParams.has('areas_page')).toBe(false);
  await directory.getByRole('searchbox').fill('Visitor area');
  await expect(directory.locator('.directory-card').first()).toContainText('Visitor area 00');
});

for (const [requested, expectedPage, expectedCards] of [['-1', '1', 24], ['2.5', '1', 24], ['9999', '2', 3]] as const) {
  test(`subdivision bookmarks normalize invalid or out-of-range page ${requested}`, async ({ page }) => {
    await areaFixture(page);
    await page.goto(`/countries/br/?areas_page=${requested}#subnational-data`, { waitUntil: 'domcontentloaded' });
    const directory = page.locator('#subnational-data');
    await directory.scrollIntoViewIfNeeded();
    await expect(directory.locator('.directory-card')).toHaveCount(expectedCards);
    await expect(directory.locator('.directory-pagination')).toContainText(`Page ${expectedPage} of 2`);
    expect(new URL(page.url()).searchParams.get('areas_page')).toBe(expectedPage === '1' ? null : expectedPage);
  });
}

test('a mobile student can retry a failed file without losing its period and format', async ({ page }) => {
  await areaFixture(page);
  await page.route('**/site-data/downloads/countries/br-sp.json', route => route.fulfill({ json: {
    schema_version: 1, generated_at: '2026-11-07', record_count: 2, parts: [{
      id: '2026-2029', label: '2026–now', is_current: true, record_count: 2,
      date_range: { start: '2026-01-01', end: '2026-02-01' },
      files: { csv: { url: 'https://files.example.test/br-sp.csv', filename: 'br-sp-2026-2029.csv', bytes: 52 } },
    }],
  } }));
  let fail = true;
  const csv = 'country_code,date,cases\nBR-SP,2026-01-01,0\nBR-SP,2026-02-01,12\n';
  await page.route('https://files.example.test/br-sp.csv', route => fail
    ? route.fulfill({ status: 503, headers: { 'access-control-allow-origin': '*' } })
    : route.fulfill({ body: csv, contentType: 'text/csv', headers: { 'access-control-allow-origin': '*' } }));
  await page.goto('/countries/br-sp/#downloads', { waitUntil: 'domcontentloaded' });
  await page.locator('#downloads').scrollIntoViewIfNeeded();
  const trigger = page.locator('#downloads [data-download-modal-trigger]');
  await trigger.click();
  const dialog = page.getByRole('dialog', { name: 'Choose data download' });
  await dialog.locator('[data-download-time-filter]').selectOption('2026-2029');
  await dialog.locator('[data-download-format-filter]').selectOption('csv');
  const button = dialog.getByRole('button', { name: 'Download CSV', exact: true });
  await button.click();
  await expect(dialog.locator('.download-file-item__status')).toContainText('Download unavailable');
  await expect(button).toBeEnabled();
  fail = false;
  const pending = page.waitForEvent('download');
  await button.click();
  const download = await pending;
  expect(download.suggestedFilename()).toBe('br-sp-2026-2029.csv');
  await expect(dialog.locator('[data-download-time-filter]')).toHaveValue('2026-2029');
  await expect(dialog.locator('[data-download-format-filter]')).toHaveValue('csv');
  await expect(dialog.locator('.download-file-item__status')).toContainText('Download started');
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
});

test('a stalled file transfer times out and permits a second download attempt', async ({ page }) => {
  await areaFixture(page);
  await page.route('**/site-data/downloads/countries/br-sp.json', route => route.fulfill({ json: {
    schema_version: 1, generated_at: '2026-11-07', record_count: 1, parts: [{
      id: '2026-2029', record_count: 1, files: { json: { url: 'https://files.example.test/slow.json', filename: 'br-sp.json' } },
    }],
  } }));
  let stall = true;
  await page.route('https://files.example.test/slow.json', route => {
    if (stall) return; // Leave this request pending until the application's deadline aborts it.
    return route.fulfill({ json: { country_code: 'BR-SP', records: [] }, headers: { 'access-control-allow-origin': '*' } });
  });
  await page.goto('/countries/br-sp/#downloads', { waitUntil: 'domcontentloaded' });
  await page.locator('#downloads').scrollIntoViewIfNeeded();
  await page.locator('#downloads [data-download-modal-trigger]').click();
  const dialog = page.getByRole('dialog', { name: 'Choose data download' });
  const button = dialog.getByRole('button', { name: 'Download JSON', exact: true });
  await expect(button).toBeVisible();
  await page.clock.install();
  await button.click();
  await expect(button).toBeDisabled();
  await page.clock.fastForward(61_000);
  await expect(dialog.locator('.download-file-item__status')).toContainText('Download unavailable');
  await expect(button).toBeEnabled();
  stall = false;
  const pending = page.waitForEvent('download');
  await button.click();
  expect((await pending).suggestedFilename()).toBe('br-sp.json');
});
