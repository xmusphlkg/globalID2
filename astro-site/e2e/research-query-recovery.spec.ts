import { expect, test } from '@playwright/test';

test('changing a research filter stops the old scan and shows only the new results', async ({ page }) => {
  test.setTimeout(90_000);
  await page.route('https://stats.globalinfectiousdisease.com/**', route => route.abort());
  const obsoleteChunks: string[] = [];
  let releaseOld = () => {};
  const oldResponses = new Promise<void>(resolve => { releaseOld = resolve; });
  const chunks = Array.from({ length: 20 }, (_, index) => ({
    path: `/research/catalogue/obsolete-${index}.json`, count: 1, facets: { disease: ['d038'] },
  }));
  const dengue = {
    article_id: 'current-filter', slug: 'current-filter', title: 'Current dengue research',
    journal: 'Test journal', published_at: '2026-01-01', detail_available: false,
    diseases: [{ disease_id: 'D021', slug: 'dengue', name_en: 'Dengue' }],
  };
  await page.route('**/research/catalogue/manifest.json', route => route.fulfill({ json: {
    chunks: [...chunks, { path: '/research/catalogue/current-filter.json', count: 1, facets: { disease: ['d021'] } }],
  } }));
  await page.route('**/research/catalogue/obsolete-*.json', async route => {
    obsoleteChunks.push(new URL(route.request().url()).pathname);
    await oldResponses;
    await route.fulfill({ json: { articles: [{ ...dengue, title: 'Obsolete influenza research', diseases: [{ disease_id: 'D038', slug: 'influenza', name_en: 'Influenza' }] }] } });
  });
  await page.route('**/research/catalogue/current-filter.json', route => route.fulfill({ json: { articles: [dengue] } }));
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto('/fr/research/?d=d038', { waitUntil: 'domcontentloaded' });
    await expect.poll(() => obsoleteChunks.length).toBe(4);
    await expect(page.locator('#research-list')).toHaveAttribute('aria-busy', 'true');
    await page.locator('#research-disease').selectOption('d021');
    expect(new URL(page.url()).searchParams.get('d')).toBe('d021');
    releaseOld();
    const list = page.locator('#research-list');
    await expect(list).toHaveAttribute('aria-busy', 'false');
    await expect(list).toBeVisible();
    await expect(list.locator('article')).toHaveCount(1);
    await expect(list).toContainText('Current dengue research');
    await expect(list).not.toContainText('Obsolete influenza research');
    await expect(page.locator('#research-load-error')).toBeHidden();
    expect(obsoleteChunks).toHaveLength(4);
    expect(errors).toEqual([]);
  } finally {
    releaseOld();
  }
});
