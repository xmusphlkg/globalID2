import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { readFile } from 'node:fs/promises';

test('searchable filters keep keyboard focus and expose the scrollable listbox accessibly', async ({ page }) => {
  test.setTimeout(60_000);
  const { map, outbreak } = JSON.parse(await readFile(new URL('./fixtures/mers-sa.json', import.meta.url), 'utf8'));
  await page.route('**/astro/runtime/client/dev-toolbar/entrypoint.js', route => route.fulfill({ body: '', contentType: 'application/javascript' }));
  await page.route('**/site-data/**', route => route.fulfill({ status: 404 }));
  await page.route('**/situation/map-data.json', route => route.fulfill({ json: map }));
  await page.route('**/situation/outbreaks/**', route => route.fulfill({ json: outbreak }));
  await page.goto('/situation/?disease=D049&window=all');
  await expect(page.locator('.sit-detail.is-open')).toHaveCount(1);
  const listToggle = page.locator('.sit-bar-links button');
  if (await listToggle.getAttribute('aria-pressed') === 'false') await listToggle.click();

  const disease = page.getByRole('combobox', { name: 'Disease', exact: true });
  await disease.click();
  const listbox = page.getByRole('listbox', { name: 'Disease', exact: true });
  await expect.poll(() => listbox.evaluate(node => node.scrollHeight > node.clientHeight)).toBe(true);
  const accessibility = await new AxeBuilder({ page }).include('.sit-search-field').withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect(accessibility.violations).toEqual([]);

  await disease.press('End');
  await expect(disease).toHaveAttribute('aria-activedescendant', await listbox.getByRole('option').last().getAttribute('id') as string);
  await expect.poll(() => listbox.evaluate(node => node.scrollTop)).toBeGreaterThan(0);
  await disease.press('Home');
  await expect(disease).toHaveAttribute('aria-activedescendant', await listbox.getByRole('option').first().getAttribute('id') as string);
  await disease.press('ArrowDown');
  const next = listbox.getByRole('option').nth(1);
  const nextValue = await next.getAttribute('data-value');
  await expect(disease).toHaveAttribute('aria-activedescendant', await next.getAttribute('id') as string);
  await disease.press('Enter');
  if ((page.viewportSize()?.width ?? 0) >= 900) await expect(disease).toBeFocused();
  await expect(listbox).toHaveCount(0);
  await expect.poll(() => new URL(page.url()).searchParams.get('disease')).toBe(nextValue);

  if (await listToggle.getAttribute('aria-pressed') === 'false') await listToggle.click();
  await disease.fill('D049');
  await disease.press('Enter');
  await expect(page.locator('.sit-detail.is-open')).toHaveCount(1);
  if (await listToggle.getAttribute('aria-pressed') === 'false') await listToggle.click();
  await disease.fill('unmatched search');
  await expect(page.getByRole('option')).toHaveCount(0);
  await disease.press('Escape');
  await expect(disease).toHaveAttribute('aria-expanded', 'false');
  await expect(disease).toBeFocused();
  await expect(page.locator('.sit-detail.is-open')).toHaveCount(1);
  await expect.poll(() => new URL(page.url()).searchParams.get('disease')).toBe('D049');
});
