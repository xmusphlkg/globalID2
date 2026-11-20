import { expect, test } from '@playwright/test';

test('Alaska and Hawaii can be selected at their geographic locations', async ({ page }) => {
  await page.goto('/countries/us/#regional-distribution', { waitUntil: 'domcontentloaded' });
  const map = page.locator('#regional-distribution');
  await map.scrollIntoViewIfNeeded();
  const focus = map.getByRole('button', { name: 'Focus on mainland' });
  const selected = map.getByRole('combobox', { name: 'Selected region' });
  await expect(focus).toHaveAttribute('aria-pressed', 'false');
  await selected.selectOption('US-CA');

  const canvas = map.locator('.subdivision-map-canvas canvas').first();
  async function selectAt(longitude: number, latitude: number, state: string) {
    const { width, height } = await canvas.evaluate(el => el.getBoundingClientRect().toJSON());
    // The 50-state view is centered at 128 W, 45 N and spans 128 degrees of longitude.
    const pxPerDegree = Math.min(width, height) * 0.87 / 128;
    await canvas.click({ position: {
      x: width * 0.54 + (longitude + 128) * pxPerDegree,
      y: height * 0.43 - (latitude - 45) * pxPerDegree / 0.75,
    } });
    await expect(selected).toHaveValue(state);
  }

  await selectAt(-152, 64, 'US-AK');
  await selectAt(-155.5, 19.6, 'US-HI');
  await focus.click();
  await map.getByRole('button', { name: 'Reset map', pressed: true }).click();
  await selectAt(-152, 64, 'US-AK');
  await selectAt(-155.5, 19.6, 'US-HI');
});
