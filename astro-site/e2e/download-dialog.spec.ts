import { createHash } from 'node:crypto';
import { expect, test } from '@playwright/test';

const catalogue = (version = 1) => ({ schema_version: 1, generated_at: `2026-11-0${version}`, record_count: 240, parts: Array.from({ length: 24 }, (_, index) => ({
  id: String(2000 + index), label: String(2000 + index), record_count: 10,
  date_range: { start: `${2000 + index}-01-01`, end: `${2000 + index}-12-31` },
  files: Object.fromEntries(['csv', 'json', 'xlsx'].map(format => [format, { url: `https://files.example.test/v${version}/${index}.${format}`, filename: `gids-${index}.${format}`, bytes: 1000 }])),
})) });

test('downloads stay compact and lazy, refresh without HTML changes, and save the selected file', async ({ page, request }) => {
  let version = 1, reads = 0;
  await page.route('**/site-data/current.json', route => route.fulfill({ status: 503 }));
  await page.route('**/site-data/downloads/countries/jp.json', route => { reads++; return route.fulfill({ json: catalogue(version) }); });
  await page.route('https://files.example.test/**', route => route.fulfill({ body: 'date,cases\n2026-01-01,10\n', contentType: 'text/csv' }));
  const htmlHash = async () => createHash('sha256').update(await (await request.get('/zh/countries/jp/')).body()).digest('hex');
  const before = await htmlHash();
  await page.goto('/zh/countries/jp/');
  await page.locator('astro-island:not([ssr]) [data-download-modal-trigger]').waitFor();
  expect(reads).toBe(0);
  await expect(page.locator('[data-download-file]')).toHaveCount(0);
  const trigger = page.locator('#downloads [data-download-modal-trigger]');
  await trigger.click();
  const dialog = page.getByRole('dialog', { name: '选择数据下载' });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('[data-download-file]')).toHaveCount(72);
  const box = await dialog.boundingBox();
  expect(box!.height).toBeLessThanOrEqual(page.viewportSize()!.height * .92);
  expect(await dialog.locator('.download-dialog__body').evaluate(element => element.scrollHeight > element.clientHeight)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await dialog.getByLabel('时间范围').selectOption('2000');
  await dialog.getByLabel('文件格式').selectOption('csv');
  await expect(dialog.locator('[data-download-file]')).toHaveCount(1);
  const saved = page.waitForEvent('download');
  await dialog.getByRole('button', { name: '下载 CSV', exact: true }).click();
  expect((await saved).suggestedFilename()).toBe('gids-0.csv');
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
  await expect(page.locator('[data-download-file]')).toHaveCount(0);
  version = 2;
  await trigger.click();
  await expect(dialog).toContainText('2026-11-02');
  await expect(dialog.locator('a[href="https://files.example.test/v2/0.csv"]')).toHaveCount(1);
  expect(reads).toBe(2);
  expect(await htmlHash()).toBe(before);
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await expect(dialog).not.toBeVisible();
});

test('a failed catalogue can be retried inside the French disease dialog', async ({ page }) => {
  let available = false;
  await page.route('**/site-data/current.json', route => route.fulfill({ status: 503 }));
  await page.route('**/site-data/downloads/diseases/d038.json', route => available ? route.fulfill({ json: catalogue() }) : route.fulfill({ status: 503 }));
  await page.goto('/fr/diseases/influenza/');
  await page.locator('astro-island:not([ssr]) [data-download-modal-trigger]').waitFor();
  await page.locator('#downloads [data-download-modal-trigger]').click();
  const dialog = page.getByRole('dialog', { name: 'Choisir un téléchargement' });
  await expect(dialog).toContainText('Informations de téléchargement indisponibles.');
  available = true;
  await dialog.getByRole('button', { name: 'Réessayer', exact: true }).click();
  await expect(dialog.getByLabel('Format de fichier')).toBeVisible();
  await dialog.getByLabel('Format de fichier').selectOption('xlsx');
  await expect(dialog.getByRole('button', { name: 'Télécharger XLSX', exact: true })).toHaveCount(24);
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
});
