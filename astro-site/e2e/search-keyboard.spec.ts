import { expect, test } from '@playwright/test';

test('complete search retains the independently published research shown in quick search', async ({ page }) => {
  test.skip(process.env.GIDS_VERIFY_RESEARCH_SEARCH !== '1', 'Run against Astro with PUBLIC_GIDS_RESEARCH_MAIN_ORIGIN=1.');
  let researchAvailable = false;
  const research = { id: 'research:flu', kind: 'research', title: { en: 'Current influenza study', zh: '流感新研究' },
    href: { en: '/research/articles/current-flu/', zh: '/zh/research/articles/current-flu/' }, aliases: ['流感'] };
  await page.route('**/site-data/search-index.json', route => route.fulfill({ json: { entries: [
    { id: 'disease:flu', kind: 'disease', title: { en: 'Influenza', zh: '流行性感冒' }, href: { en: '/diseases/influenza/' }, aliases: ['流感'] },
    { ...research, id: 'research:stale', title: { en: 'Old influenza study', zh: '流感旧研究' } },
  ] } }));
  await page.route('**/research/search-index.json', route => researchAvailable
    ? route.fulfill({ json: { entries: [research] } })
    : route.fulfill({ status: 503 }));
  await page.goto('/zh/search/?q=流感');
  await expect(page.locator('#search-page-status')).toContainText('暂时无法加载搜索');
  await expect(page.locator('.search-result-card')).toHaveCount(0);
  researchAvailable = true;
  await page.locator('#search-page-status').getByRole('button', { name: '重试' }).click();
  await expect(page.locator('.search-result-card')).toHaveCount(2);
  researchAvailable = false;
  await page.locator('#site-search-open').click();
  await page.locator('#site-search-input').fill('流感');
  await expect(page.locator('#site-search-feedback')).toContainText('暂时无法加载搜索');
  await expect(page.locator('.site-search-result')).toHaveCount(0);
  researchAvailable = true;
  await page.locator('#site-search-feedback').getByRole('button', { name: '重试' }).click();
  await expect(page.locator('.site-search-result')).toHaveCount(2);
  await expect(page.locator('.site-search-result strong', { hasText: '流感新研究' })).toBeVisible();
  await expect(page.locator('.site-search-result strong', { hasText: '流感旧研究' })).toHaveCount(0);
  await page.locator('#site-search-all').click();
  await expect(page.locator('#search-page-input')).toHaveValue('流感');
  await expect(page.locator('.search-result-card')).toHaveCount(2);
  await expect(page.locator('.search-result-card', { hasText: '流感新研究' })).toHaveAttribute('href', '/zh/research/articles/current-flu/');
  await expect(page.locator('.search-result-card strong', { hasText: '流感旧研究' })).toHaveCount(0);
});

for (const prefix of ['', '/zh', '/fr']) {
  test(`quick search Enter opens complete results in ${prefix || 'en'}`, async ({ page }) => {
    await page.route('**/research/search-index.json', route => route.fulfill({ json: { entries: [] } }));
    await page.route('**/site-data/search-index.json', route => route.fulfill({ json: { entries: [
      { id: 'disease:flu', kind: 'disease', title: { en: 'Influenza' }, href: { en: '/diseases/influenza/' }, aliases: ['flu'] },
    ] } }));
    await page.goto(`${prefix}/search/`);
    await page.locator('#site-search-open').click();
    const input = page.locator('#site-search-input');
    await input.fill('f');
    await input.press('Enter');
    await expect(page.locator('#site-search-dialog')).toBeVisible();
    await input.fill('flu');
    await expect(page.locator('.site-search-result')).toHaveCount(1);
    await input.press('Enter');
    await expect(page).toHaveURL(new RegExp(`${prefix}/search/\\?q=flu$`));
    await expect(page.locator('#search-page-input')).toHaveValue('flu');
    await expect(page.locator('.search-result-card strong')).toHaveText('Influenza');
    await page.locator('#site-search-open').click();
    await page.locator('.site-search-close').click();
    await expect(page.locator('#site-search-dialog')).not.toBeVisible();
    await expect(page.locator('#site-search-open')).toBeFocused();
  });
}
