import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

const canonicalRoutes = [
  '/',
  '/countries/au/',
  '/diseases/dengue/',
  '/situation/',
  '/research/',
  '/countries/jp/reports/50/',
  '/search/?q=dengue',
  '/copyright/',
  '/zh/',
  '/zh/about/',
  '/zh/copyright/',
  '/zh/downloads/',
  '/zh/research/',
  '/zh/countries/',
  '/zh/diseases/',
  '/zh/situation/',
  '/zh/situation/methodology/',
  '/zh/research/ask/',
  '/zh/research/graph/',
  '/zh/research/integrity/',
  '/zh/changelog/',
  '/zh/subscribe/',
  '/zh/terms/',
];

test('canonical public routes render without horizontal overflow', async ({ page }, testInfo) => {
  const routes = testInfo.project.name.endsWith('-390') || testInfo.project.name.endsWith('-1280')
    ? canonicalRoutes
    : ['/', '/diseases/dengue/', '/zh/research/'];
  for (const route of routes) {
    const response = await page.goto(route, { waitUntil: 'domcontentloaded' });
    expect(response?.ok(), route).toBeTruthy();
    await expect(page.locator('h1').first()).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `${route} horizontal overflow`).toBeLessThanOrEqual(1);
  }
});

test('mobile navigation closes with Escape and preserves focus', async ({ page }, testInfo) => {
  test.skip(!['chromium-360', 'chromium-390', 'chromium-768'].includes(testInfo.project.name));
  await page.goto('/');
  const toggle = page.locator('#mobile-menu-toggle');
  await expect(toggle).toHaveAccessibleName('Open navigation menu');
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(toggle).toHaveAccessibleName('Close navigation menu');
  await page.keyboard.press('Escape');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(toggle).toBeFocused();
});

test('locale switch changes URL, document language, title, and canonical', async ({ page }) => {
  await page.goto('/diseases/dengue/');
  const desktopLocaleLink = page.locator('#lang-toggle');
  if (await desktopLocaleLink.isVisible()) {
    await desktopLocaleLink.click();
  } else {
    await page.locator('#mobile-menu-toggle').click();
    await page.locator('.site-mobile-tools a').click();
  }
  await expect(page).toHaveURL(/\/zh\/diseases\/dengue\/$/);
  await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN');
  await expect(page).toHaveTitle(/登革热/);
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', /\/zh\/diseases\/dengue\/$/);
});

test('mobile header keeps large controls and overlays navigation without shifting content', async ({ page }, testInfo) => {
  test.skip(!['chromium-360', 'chromium-390'].includes(testInfo.project.name));
  await page.goto('/');
  const controls = page.locator('#site-search-open, #theme-toggle, #mobile-menu-toggle');
  await expect(controls).toHaveCount(3);
  for (const control of await controls.all()) {
    const box = await control.boundingBox();
    expect(box?.width).toBeGreaterThanOrEqual(44);
    expect(box?.height).toBeGreaterThanOrEqual(44);
  }
  const mainTopBefore = await page.locator('#main-content').evaluate(element => element.getBoundingClientRect().top);
  await page.locator('#mobile-menu-toggle').click();
  await expect(page.locator('#mobile-menu')).toBeVisible();
  await expect(page.locator('.site-mobile-tools a')).toBeVisible();
  const mainTopAfter = await page.locator('#main-content').evaluate(element => element.getBoundingClientRect().top);
  expect(mainTopAfter).toBe(mainTopBefore);
});

test('command search supports keyboard access and locale-aware results', async ({ page }) => {
  await page.goto('/');
  await page.keyboard.press('Control+k');
  await expect(page.getByRole('dialog', { name: 'Find data and evidence' })).toBeVisible();
  await page.getByRole('searchbox', { name: 'Search countries, diseases, reports, and research' }).fill('dengue');
  await expect(page.locator('.site-search-result').first()).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog', { name: 'Find data and evidence' })).not.toBeVisible();
});

test('shared public pages render complete Chinese structures', async ({ page }, testInfo) => {
  test.skip(!['chromium-390', 'chromium-1280'].includes(testInfo.project.name));
  await page.goto('/zh/about/');
  await expect(page.getByRole('heading', { level: 1, name: '每条证据都可以追溯到来源' })).toBeVisible();
  await expect(page.getByRole('heading', { level: 2, name: '数据如何进入网站' })).toBeVisible();
  await page.goto('/zh/downloads/');
  await expect(page.getByRole('heading', { level: 1, name: '数据下载' })).toBeVisible();
  expect(await page.locator('[data-download-row]').count()).toBeGreaterThan(100);
  await page.getByRole('searchbox', { name: '搜索数据集' }).fill('cholera');
  await expect(page.locator('[data-download-row]:visible')).toHaveCount(1);
});

test('copyright center exposes reuse guidance, material rules, and citation tools', async ({ page }, testInfo) => {
  test.skip(!['chromium-390', 'chromium-1280'].includes(testInfo.project.name));
  await page.goto('/copyright/');
  await expect(page.getByRole('heading', { level: 1, name: 'Copyright, licensing & reuse' })).toBeVisible();
  await expect(page.getByRole('heading', { level: 2, name: 'Using GIDS at a glance' })).toBeVisible();
  await expect(page.getByRole('heading', { level: 2, name: 'What rules apply?' })).toBeVisible();
  await expect(page.locator('#reuse-checker tbody tr')).toHaveCount(8);
  await expect(page.getByRole('tab', { name: 'BibTeX' })).toBeVisible();
  await page.getByRole('tab', { name: 'RIS' }).click();
  await expect(page.getByRole('tabpanel')).toContainText('TY  - DATA');
  await expect(page.getByRole('link', { name: /Read the official licence deed/ })).toHaveAttribute('href', 'https://creativecommons.org/licenses/by/4.0/');
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);
  const axeResults = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
  expect(axeResults.violations.filter(item => ['critical', 'serious'].includes(item.impact ?? ''))).toEqual([]);

  await page.goto('/zh/copyright/');
  await expect(page.getByRole('heading', { level: 1, name: '版权、许可与复用说明' })).toBeVisible();
  await expect(page.getByRole('heading', { level: 2, name: 'GIDS 内容可以怎样使用？' })).toBeVisible();
  await expect(page.getByRole('heading', { level: 2, name: '不同材料适用什么规则？' })).toBeVisible();
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', /\/zh\/copyright\/$/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(1);
});

test('Chinese country and disease routes reuse the complete data templates', async ({ page }, testInfo) => {
  test.skip(!['chromium-390', 'chromium-1280'].includes(testInfo.project.name));
  await page.goto('/zh/countries/au/');
  await expect(page.getByRole('heading', { level: 1, name: '澳大利亚' })).toBeVisible();
  await expect(page.getByRole('heading', { level: 2, name: '核心监测指标' })).toBeVisible();
  await expect(page.getByRole('heading', { level: 2, name: '各疾病监测趋势' })).toBeVisible();
  await expect(page.locator('.country-flag img').first()).toHaveAttribute('src', '/flags/au.svg');
  await expect(page.locator('.figure-panel')).toHaveCount(7);
  await expect(page.locator('#downloads')).toBeAttached();
  await expect(page.locator('#disease-directory a').first()).toHaveAttribute('href', /^\/zh\/diseases\//);
  await expect(page.getByRole('button', { name: '全部', exact: true })).toBeVisible();
  await expect(page.locator('#disease-comparison-table tbody a').first()).toHaveAttribute('href', /^\/zh\/diseases\//);

  await page.goto('/zh/diseases/dengue/');
  await expect(page.getByRole('heading', { level: 1, name: '登革热' })).toBeVisible();
  await expect(page.getByRole('heading', { level: 2, name: '报告国家和地区' })).toBeVisible();
  await expect(page.getByRole('heading', { level: 2, name: '各报告国家的趋势' })).toBeVisible();
  await expect(page.getByRole('heading', { level: 2, name: '近期相关研究' })).toBeVisible();
  await expect(page.locator('.figure-panel')).toHaveCount(5);
  await expect(page.locator('.reporting-country-link').first()).toHaveAttribute('href', /^\/zh\/countries\//);
  await expect(page.locator('#downloads')).toBeAttached();
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', /\/zh\/diseases\/dengue\/$/);
});

test('Chinese country-disease and report routes reuse the complete report templates', async ({ page }, testInfo) => {
  test.skip(!['chromium-390', 'chromium-1280'].includes(testInfo.project.name));

  await page.goto('/zh/countries/au/diseases/hepatitis-c/');
  await expect(page.locator('#main-content h1').first()).toContainText('澳大利亚');
  await expect(page.getByRole('heading', { level: 2, name: '数据与限制' })).toBeVisible();
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', /\/zh\/countries\/au\/diseases\/hepatitis-c\/$/);

  await page.goto('/zh/countries/jp/reports/');
  await expect(page.getByRole('heading', { level: 1, name: /日本.*报告/ })).toBeVisible();
  await expect(page.locator('a[data-seo-event="report_open"]').first()).toHaveAttribute('href', /^\/zh\/countries\/jp\/reports\//);

  await page.goto('/zh/countries/jp/reports/50/');
  await expect(page.getByRole('heading', { level: 2, name: '当前判断与下一步动作' })).toBeVisible();
  await expect(page.locator('a[href^="/zh/countries/jp/reports/50/"]').first()).toBeAttached();
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', /\/zh\/countries\/jp\/reports\/50\/$/);

  await page.goto('/zh/countries/jp/reports/50/hepatitis-a/');
  await expect(page.getByRole('heading', { level: 1, name: '甲型肝炎' })).toBeVisible();
  await expect(page.getByRole('link', { name: '返回报告' })).toHaveAttribute('href', '/zh/countries/jp/reports/50/');
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', /\/zh\/countries\/jp\/reports\/50\/hepatitis-a\/$/);
});

test('Chinese Research collections reuse the complete locale-aware templates', async ({ page }, testInfo) => {
  test.skip(!['chromium-390', 'chromium-1280'].includes(testInfo.project.name));

  for (const route of [
    '/zh/research/diseases/dengue/',
    '/zh/research/countries/au/',
    '/zh/research/topics/surveillance/',
    '/zh/research/weekly/2026-W34/',
    '/zh/research/preprints/',
  ]) {
    const response = await page.goto(route);
    expect(response?.ok(), route).toBeTruthy();
    await expect(page.getByText('证据流', { exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { level: 2, name: '已发布文献' })).toBeVisible();
    await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', new RegExp(`${route.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`));
    const articleLink = page.locator('.research-card-title a').first();
    if (await articleLink.count()) await expect(articleLink).toHaveAttribute('href', /^\/zh\/research\/articles\//);
  }

  await page.goto('/zh/research/articles/10-1093-jtm-taag065-7be8baca/');
  await expect(page.getByRole('heading', { level: 2, name: '结构化证据摘要' })).toBeVisible();
  await expect(page.getByText('为何这项研究现在值得关注', { exact: true })).toBeVisible();
  await expect(page.locator('.topic-tags a').first()).toHaveAttribute('href', /^\/zh\/research\/topics\//);
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', /\/zh\/research\/articles\/10-1093-jtm-taag065-7be8baca\/$/);
});

test('Chinese indexes, tools, Situation, and static services use route-native templates', async ({ page }, testInfo) => {
  test.skip(!['chromium-390', 'chromium-1280'].includes(testInfo.project.name));

  const routes: Array<[string, string]> = [
    ['/zh/countries/', '数据覆盖国家/地区'],
    ['/zh/diseases/', '疾病目录'],
    ['/zh/situation/', '全球传染病态势'],
    ['/zh/situation/methodology/', 'GIDS 如何筛查传染病信号'],
    ['/zh/research/', '研究雷达'],
    ['/zh/research/ask/', '问研究雷达'],
    ['/zh/research/graph/', '研究证据图谱'],
    ['/zh/research/integrity/', '研究完整性通知'],
    ['/zh/changelog/', 'GIDS 版本更新'],
    ['/zh/subscribe/', '接收你关注的传染病监测更新'],
    ['/zh/terms/', 'GIDS 服务协议与隐私说明'],
  ];

  for (const [route, heading] of routes) {
    const response = await page.goto(route, { waitUntil: 'domcontentloaded' });
    expect(response?.ok(), route).toBeTruthy();
    await expect(page.locator('h1').first()).toContainText(heading);
    await expect(page.locator('html')).toHaveAttribute('lang', 'zh-CN');
    await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', new RegExp(`${route.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`));
    expect(await page.locator('[data-lang-en], [data-lang-zh]').count(), route).toBe(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth), route).toBeLessThanOrEqual(1);
  }

  await page.goto('/zh/countries/');
  expect(await page.locator('.country-flag img').count()).toBeGreaterThan(0);
  await expect.poll(() => page.locator('.country-flag img').evaluateAll(images => (
    images.every(image => image instanceof HTMLImageElement && (!image.complete || image.naturalWidth > 0))
  ))).toBe(true);
  await expect(page.locator('.country-card').first()).toHaveAttribute('href', /^\/zh\/countries\//);
  await page.goto('/zh/diseases/');
  await expect(page.locator('.disease-card-wrap a').first()).toHaveAttribute('href', /^\/zh\/diseases\//);
});

test('long technical sections use progressive disclosure', async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== 'chromium-1280');
  await page.goto('/diseases/cholera/');
  await expect(page.locator('.disease-profile-details')).not.toHaveAttribute('open', '');
  await expect(page.locator('.source-register')).not.toHaveAttribute('open', '');
  await page.goto('/changelog/');
  await expect(page.locator('.changelog-release-body[open]')).toHaveCount(5);
  const lastRelease = page.locator('.changelog-release').last();
  const lastId = await lastRelease.getAttribute('id');
  await page.goto(`/changelog/#${lastId}`);
  await expect(page.locator(`[id="${lastId}"] .changelog-release-body`)).toHaveAttribute('open', '');
  await page.goto('/research/');
  await expect(page.locator('#research-list .research-card')).toHaveCount(8);
});

test('theme persists and key pages have no critical or serious Axe findings', async ({ page }, testInfo) => {
  test.skip(!['chromium-390', 'chromium-1280'].includes(testInfo.project.name));
  await page.goto('/');
  await page.getByRole('button', { name: /Switch to (dark|light) theme/ }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.evaluate(() => localStorage.setItem('theme', 'light'));
  await page.reload();
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
  expect(results.violations.filter(item => ['critical', 'serious'].includes(item.impact ?? ''))).toEqual([]);
});
