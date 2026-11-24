import { expect, test } from '@playwright/test';

const lists = ['reports', 'alerts', 'weekly_digest', 'research_digest'].map(code => ({
  code, name_en: code, name_zh: code, available: true,
}));

for (const [prefix, titles, count, overlap] of [
  ['', ['Surveillance reports', 'Official outbreak notices', 'Weekly report digest', 'Research Radar'], '2 selected', 'both include reports'],
  ['/zh', ['监测报告', '官方疫情通报', '报告周摘要', '研究雷达'], '已选 2 项', '均包含报告内容'],
  ['/fr', ['Rapports de surveillance', 'Communiqués épidémiologiques', 'Récapitulatif des rapports', 'Veille scientifique'], '2 sélections', 'contiennent tous deux des rapports'],
] as const) {
  test(`formal subscription choices explain content, frequency and selection (${prefix || 'en'})`, async ({ page }) => {
    await page.route(/(\/__dev\/subscription-options|\/api\/subscriptions\/options)$/, route => route.fulfill({
      json: { ok: true, pilot_mode: false, delivery_mode: 'full', lists,
        locales: [{ value: 'en', label_en: 'English' }, { value: 'zh', label_en: 'Chinese', label_zh: '中文' }], filters: {} },
    }));
    await page.goto(`${prefix}/subscribe/`, { waitUntil: 'domcontentloaded' });
    const cards = page.locator('#list-options .subscribe-content-option');
    await expect(cards).toHaveCount(4);
    await expect(page.locator('#subscription-pilot-notice')).toBeHidden();
    for (const [index, title] of titles.entries()) {
      await expect(cards.nth(index).locator('strong')).toHaveText(title);
      await expect(cards.nth(index).locator('.subscribe-check-description')).not.toBeEmpty();
      await expect(cards.nth(index).locator('.subscribe-check-frequency')).not.toBeEmpty();
    }
    await expect(page.locator('input[name="list_codes"][value="reports"]')).toBeChecked();
    await expect(page.locator('#subscription-content-overlap')).toBeHidden();
    await page.locator('input[name="list_codes"][value="weekly_digest"]').check();
    await expect(page.locator('#subscription-selection-count')).toHaveText(count);
    await expect(page.locator('#subscription-content-overlap')).toContainText(overlap);
    await page.locator('input[name="list_codes"][value="reports"]').uncheck();
    await expect(page.locator('#subscription-content-overlap')).toBeHidden();
    const layout = await cards.evaluateAll(elements => elements.map(element => {
      const box = element.getBoundingClientRect();
      const frequency = element.querySelector('.subscribe-check-frequency')!.getBoundingClientRect();
      return { width: element.clientWidth, scroll: element.scrollWidth, left: box.left, right: box.right,
        bottom: box.bottom, frequencyBottom: frequency.bottom };
    }));
    expect(layout.every(card => card.scroll <= card.width + 1 && card.left >= 0 && card.right <= page.viewportSize()!.width)).toBe(true);
    expect(layout.every(card => card.frequencyBottom <= card.bottom - 10)).toBe(true);
    await page.locator('#list-options').screenshot({ path: `../exports/subscription-content-${prefix.slice(1) || 'en'}-${page.viewportSize()!.width}.png` });
  });
}
