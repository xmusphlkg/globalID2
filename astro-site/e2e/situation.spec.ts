import { expect, test } from '@playwright/test';

test('situation page: select a place, expand timeline cards, graph stays attached', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));

  await page.goto('/situation/');
  const items = page.locator('.sit-item');
  await expect(items.first()).toBeVisible({ timeout: 30_000 });

  await items.first().click();
  const detail = page.locator('.sit-detail.is-open');
  await expect(detail).toBeVisible();

  const alerts = detail.locator('.sit-alert');
  await expect(alerts.first()).toBeVisible({ timeout: 15_000 });

  const rows = Math.min(await alerts.count(), 4);
  for (let index = 0; index < rows; index += 1) {
    const row = alerts.nth(index);
    const summary = row.locator('summary');
    if (!(await row.locator('details').evaluate(node => (node as HTMLDetailsElement).open))) await summary.click();
    const box = await row.boundingBox();
    const graph = await row.locator('.sit-graph').boundingBox();
    expect(box).not.toBeNull();
    expect(graph).not.toBeNull();
    // The graph column must span the whole expanded row, with no 150px SVG padding either.
    expect(Math.abs(graph!.height - box!.height)).toBeLessThan(2);
  }

  const first = await alerts.first().boundingBox();
  expect(first!.height).toBeLessThan(700);
  await detail.screenshot({ path: testInfo.outputPath('timeline.png') });
  expect(errors).toEqual([]);
});

test('situation page: filtering recentres the map without errors', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));

  await page.goto('/situation/');
  await expect(page.locator('.sit-item').first()).toBeVisible({ timeout: 30_000 });
  const country = page.locator('.sit-filter-grid select').nth(1);
  const options = await country.locator('option').allTextContents();
  test.skip(options.length < 2, 'no country facets in the local data');
  await country.selectOption({ index: 1 });
  await expect(page.locator('.sit-canvas canvas').first()).toBeVisible();
  await page.getByRole('button', { name: /重置|Reset|Réinitialiser/ }).click();
  expect(errors).toEqual([]);
});
