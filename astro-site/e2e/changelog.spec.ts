import { expect, test } from '@playwright/test';

const release = (version: string, date = '2026-11-14') => ({
  version, date, titleEn: 'Current release', titleZh: '当前发布', titleFr: 'Version actuelle',
  summaryEn: 'Published independently.', summaryZh: '独立发布的版本记录。', summaryFr: 'Publication indépendante.',
  sections: [{ kind: 'fixed', labelEn: 'Fixed', labelZh: '修复', labelFr: 'Corrections',
    items: [{ en: 'Updated navigation.', zh: '统一导航。', fr: 'Navigation unifiée.' }] }],
});

test('new version records appear without rebuilding or reloading the page', async ({ page }) => {
  await page.clock.install();
  let releases = [release('90.0.0'), release('89.0.0', '2026-11-13')];
  let available = true;
  await page.route('**/site-data/updates/changelog.json', route => available
    ? route.fulfill({ json: { schema_version: 1, releases } }) : route.fulfill({ status: 503 }));
  const response = await page.goto('/zh/changelog/#v89.0.0');
  expect(await response!.text()).not.toContain('90.0.0');
  await expect(page.locator('[id="v89.0.0"] details')).toHaveAttribute('open', '');
  await expect(page.locator('.changelog-latest-card strong')).toHaveText('v90.0.0');
  releases = [release('91.0.0'), ...releases];
  await page.clock.fastForward(61_000);
  await expect(page.locator('.changelog-latest-card strong')).toHaveText('v91.0.0');
  available = false;
  await page.clock.fastForward(61_000);
  await expect(page.getByRole('status')).toContainText('版本记录暂不可用');
  await expect(page.locator('.changelog-release')).toHaveCount(3);
  available = true;
  await page.getByRole('button', { name: '重试', exact: true }).click();
  await expect(page.getByRole('button', { name: '重试', exact: true })).toHaveCount(0);
});

test('the same runtime history supplies all three languages and update counts', async ({ page }) => {
  let releases = [release('90.0.0')];
  await page.route('**/site-data/updates/changelog.json', route => route.fulfill({ json: { schema_version: 1, releases } }));
  await page.route('**/site-data/updates/latest.json', route => route.fulfill({ json: { schema_version: 1, months: [] } }));
  for (const [prefix, title] of [['', 'Current release'], ['/zh', '当前发布'], ['/fr', 'Version actuelle']]) {
    await page.goto(`${prefix}/changelog/`);
    await expect(page.locator('.changelog-release h2')).toHaveText(title);
    await expect(page.locator('footer.site-footer')).toHaveCount(1);
  }
  await page.clock.install();
  await page.goto('/zh/data-updates/');
  await expect(page.locator('.updates-kind').filter({ hasText: 'v90.0.0' })).toHaveCount(1);
  releases = [release('91.0.0'), ...releases];
  await page.clock.fastForward(61_000);
  await expect(page.locator('.updates-kind').filter({ hasText: 'v91.0.0' })).toHaveCount(1);
  await expect(page.locator('.updates-metrics .site-stat-item').filter({ hasText: '已记录的网站版本' }).locator('strong')).toHaveText('2');
});
