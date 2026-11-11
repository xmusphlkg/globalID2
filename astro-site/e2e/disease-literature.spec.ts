import { expect, test, type Page } from '@playwright/test';
import { literatureIndexRecord } from '../src/lib/research-literature';

const article = (index: number, extra: Record<string, unknown> = {}) => ({
  article_id: `library-${index}`, slug: `library-${index}`, title: `Influenza study ${index}`, doi: `10.1000/library-${index}`,
  published_at: '2026-02-01', journal: 'Example journal', authors: ['First Author', 'Second Author', 'Third Author', 'Fourth Author', 'Fifth Searchable'],
  study_type: 'Randomised controlled trial', detail_available: true, peer_review_status: 'peer_reviewed', open_access_status: 'open',
  diseases: [{ disease_id: 'D038', slug: 'influenza', name_en: 'Influenza', name_zh: '流行性感冒' }],
  countries: [{ code: 'JP', name_en: 'Japan', name_zh: '日本' }], topics: [{ name: 'Vaccination' }],
  summary: { en: { main_findings: 'Published evidence.' }, zh: { main_findings: '已发表的研究结果。' } }, ...extra,
});

async function pagedLibrary(page: Page, records: any[], failSecondPage = false) {
  const shards = Array.from({ length: Math.ceil(records.length / 24) }, (_, i) => ({ id: `${i}-aaaaaaaaaaaaaaaa`, url: `/research/catalogue/diseases/d038/${i}-aaaaaaaaaaaaaaaa.json`, articles: records.slice(i * 24, (i + 1) * 24) }));
  const requested: string[] = [];
  await page.route('**/research/catalogue/diseases/d038.json', route => route.fulfill({ json: {
    schema_version: 2, disease_id: 'd038', total: records.length, page_size: 24,
    records: shards.flatMap(shard => shard.articles.map(article => literatureIndexRecord(article, shard.id))),
    shards: shards.map(({ id, url, articles }) => ({ id, url, count: articles.length })),
  } }));
  await page.route('**/research/catalogue/diseases/d038/*.json', route => {
    const shard = shards.find(shard => route.request().url().endsWith(shard.url))!;
    requested.push(shard.id);
    if (failSecondPage && shard.id.startsWith('1-')) { failSecondPage = false; return route.fulfill({ status: 503 }); }
    return route.fulfill({ json: { schema_version: 2, disease_id: 'd038', shard: shard.id, articles: shard.articles } });
  });
  return requested;
}

test('disease links open a dedicated literature page with transparent database queries', async ({ page }) => {
  test.setTimeout(90_000);
  await page.goto('/zh/diseases/influenza/', { timeout: 60_000 });
  const allLiterature = page.locator('.disease-research-panel a').filter({ hasText: /查看全部.*篇文献/ });
  await expect(allLiterature).toHaveAttribute('href', '/zh/research/diseases/influenza/');
  await allLiterature.click();
  await expect(page.locator('h1')).toContainText('流行性感冒文献');
  await expect(page.locator('#literature-trend-title')).toHaveText('发文趋势');
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', /\/zh\/research\/diseases\/influenza\/$/);
  const pubmed = page.locator('.literature-database-links a').filter({ hasText: 'PubMed' });
  const query = new URL((await pubmed.getAttribute('href'))!).searchParams.get('term');
  expect(query).toContain('"Influenza"[Title/Abstract]');
  expect(query).toContain('"flu"[Title/Abstract]');
  await page.getByText('查看与复制检索式', { exact: true }).click();
  await expect(page.locator('#strategy-PubMed')).toHaveValue(query!);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
});

test('complete disease records, filters and yearly chart stay in sync and validate year bounds', async ({ page }) => {
  test.setTimeout(90_000);
  const records = Array.from({ length: 30 }, (_, i) => article(i, i < 20 ? {} : {
    published_at: '2020-03-01', detail_available: false,
    countries: [{ code: 'BR', name_en: 'Brazil', name_zh: '巴西' }],
  }));
  const requested = await pagedLibrary(page, records);
  await page.goto('/zh/research/diseases/influenza/', { timeout: 60_000 });
  const library = page.locator('[data-literature-explorer]');
  await expect(library).toHaveAttribute('data-catalogue-ready', 'true', { timeout: 15_000 });
  await expect(library.locator('[data-literature-record]')).toHaveCount(24);
  expect(requested).toEqual(['0-aaaaaaaaaaaaaaaa']);
  await library.getByRole('button', { name: '下一页' }).click();
  await expect(library.locator('[data-literature-record]')).toHaveCount(6);
  await expect(page).toHaveURL(/page=2/);
  expect(requested).toEqual(['0-aaaaaaaaaaaaaaaa', '1-aaaaaaaaaaaaaaaa']);
  await page.reload();
  await expect(library.locator('[data-literature-record]')).toHaveCount(6);
  await expect(library.locator('.literature-result-count')).toContainText('第 2 / 2 页');
  await library.getByLabel('起始年份', { exact: true }).fill('2025');
  await library.getByLabel('截止年份', { exact: true }).fill('2026');
  await expect(library.locator('.literature-result-count')).toContainText('符合条件 20 篇');
  await expect(page).not.toHaveURL(/page=2/);
  await library.getByText('查看各年数量', { exact: true }).click();
  await expect(library.locator('.literature-trend-table tbody')).toContainText('2026');
  await expect(library.locator('.literature-trend-table tbody td')).toHaveText('20');
  await library.getByLabel('国家或地区', { exact: false }).selectOption('BR');
  await expect(library.locator('.literature-empty')).toContainText('没有符合条件的文献');
  await library.getByLabel('截止年份', { exact: true }).fill('2020');
  await expect(library.getByRole('alert')).toContainText('起始年份不能晚于截止年份');
  await library.getByRole('button', { name: '重置筛选' }).click();
  await library.getByLabel('在该疾病文献中搜索', { exact: true }).fill('Fifth Searchable');
  await expect(library.locator('.literature-result-count')).toContainText('符合条件 30 篇');
  await library.getByLabel('记录类型', { exact: false }).selectOption('bibliographic');
  await expect(library.locator('[data-literature-record]')).toHaveCount(10);
  await expect(library.locator('[data-literature-record] a').first()).toHaveAttribute('href', /^https:\/\/doi.org\//);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
});

test('page failures hide old cards and can be retried without downloading the whole library', async ({ page }) => {
  test.setTimeout(90_000);
  const requested = await pagedLibrary(page, Array.from({ length: 30 }, (_, i) => article(i, { published_at: new Date(Date.UTC(2026, 1, 1) - i * 86_400_000).toISOString().slice(0, 10) })), true);
  await page.goto('/zh/research/diseases/influenza/', { timeout: 60_000 });
  const library = page.locator('[data-literature-explorer]');
  await expect(library.locator('[data-literature-record]')).toHaveCount(24);
  await expect(library.getByRole('button', { name: '下一页' })).toBeEnabled();
  await library.getByRole('button', { name: '下一页' }).click();
  await expect(library.getByRole('button', { name: '重试本页' })).toBeVisible();
  await expect(library.locator('[data-literature-record]')).toHaveCount(0);
  await library.getByRole('button', { name: '重试本页' }).click();
  await expect(library.locator('[data-literature-record]')).toHaveCount(6);
  expect(requested).toEqual(['0-aaaaaaaaaaaaaaaa', '1-aaaaaaaaaaaaaaaa', '1-aaaaaaaaaaaaaaaa']);
});

test('focused Ask loads one complete scoped collection without fetching worldwide catalogue shards', async ({ page }) => {
  test.setTimeout(90_000);
  const collections: string[] = [];
  await page.route('**/research/catalogue/entities.json', route => route.fulfill({ json: { schema_version: 1, entities: [
    { scope: 'disease', value: 'd038', aliases: ['Influenza', 'flu', '流感'], path: '/site-data/research/diseases/d038.json' },
  ] } }));
  await page.route('**/site-data/research/diseases/d038.json', async route => {
    collections.push(route.request().url());
    await new Promise(resolve => setTimeout(resolve, 150));
    await route.fulfill({ json: { count: 87, articles: Array.from({ length: 87 }, (_, i) => article(i)) } });
  });
  const worldwide: string[] = [];
  page.on('request', request => { if (/\/research\/catalogue\/(?:manifest|\d[^/]*)\.json/.test(request.url())) worldwide.push(request.url()); });
  await page.goto('/zh/research/ask/?q=日本流感', { timeout: 60_000 });
  await expect(page.locator('#answer-query-caption')).toContainText('日本流感');
  await expect(page.locator('#ask-answer')).toBeVisible();
  await expect(page.locator('#answer-exact-count')).not.toHaveText('0');
  expect(collections).toHaveLength(1);
  expect(worldwide).toHaveLength(0);
});

test('generic Ask shows complete-catalogue progress, bounds requests and ignores an older search finishing late', async ({ page }) => {
  test.setTimeout(90_000);
  await page.route('**/research/catalogue/entities.json', route => route.fulfill({ json: { entities: [
    { scope: 'disease', value: 'd038', aliases: ['流感'], path: '/site-data/research/diseases/d038.json' },
  ] } }));
  const chunks = Array.from({ length: 7 }, (_, index) => ({ path: `/research/catalogue/generic-race-${index}.json`, count: 1, facets: {} }));
  await page.route('**/research/catalogue/manifest.json', route => route.fulfill({ json: { chunks } }));
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let requested = 0, active = 0, maximum = 0;
  await page.route('**/research/catalogue/generic-race-*.json', async route => {
    requested++; maximum = Math.max(maximum, ++active);
    await gate;
    await route.fulfill({ json: { articles: [article(requested, { title: 'unstructured exposure' })] } });
    active--;
  });
  await page.route('**/site-data/research/diseases/d038.json', route => route.fulfill({ json: { articles: [article(1000)] } }));
  await page.goto('/zh/research/ask/?q=unstructured%20exposure', { timeout: 60_000 });
  await expect.poll(() => requested).toBe(4);
  await expect(page.locator('#ask-status')).toContainText('已读取 0 / 7');
  await page.locator('#research-question').fill('日本流感');
  await page.locator('#research-question-form').evaluate(form => (form as HTMLFormElement).requestSubmit());
  await expect(page.locator('#answer-query-caption')).toContainText('日本流感');
  await expect(page.locator('#ask-status')).toBeHidden();
  release();
  await expect.poll(() => requested).toBe(7);
  await expect.poll(() => active).toBe(0);
  expect(maximum).toBeLessThanOrEqual(4);
  await expect(page.locator('#answer-query-caption')).toContainText('日本流感');
  await expect(page.locator('#ask-status')).toBeHidden();
  await expect(page.locator('#answer-exact-count')).toHaveText('1');
});
