import { expect, test } from '@playwright/test';

for (const [locale, country, disease] of [['zh', '区域', '疾病'], ['fr', 'zone', 'maladie']]) {
  test(`${locale} graph localizes node labels during filtering and relationship selection`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`/${locale}/research/graph/`);
    const root = page.locator('[data-evidence-graph]');
    const type = root.locator('[data-graph-type]');
    const nodes = root.locator('[data-graph-node-list] button');
    await expect(nodes.first()).toBeVisible();
    for (const [value, label] of [['country', country], ['disease', disease]]) {
      await type.selectOption(value);
      await expect(nodes.first().locator('small')).toHaveText(label);
      await nodes.first().hover();
      await expect(nodes.first().locator('span')).not.toBeEmpty();
      await nodes.first().click();
      await expect(nodes.first()).toHaveAttribute('aria-pressed', 'true');
      await expect(root.locator('[data-graph-detail] h3')).toHaveText(await nodes.first().locator('span').innerText());
      await expect(root.locator('[data-graph-detail] > p')).toContainText(label);
    }
    expect(errors).toEqual([]);
  });
}
