import { expect, test, type Page } from '@playwright/test';

async function cardAppearance(page: Page, path: string) {
  const response = await page.goto(path, { waitUntil: 'domcontentloaded', timeout: 90_000 });
  expect(response?.status()).toBe(200);
  const result = await page.waitForFunction(() => {
    const list = document.querySelector('.research-list');
    const cards = list?.querySelectorAll(':scope > article');
    if (!list || !cards || cards.length < 2 || getComputedStyle(list).display !== 'grid') return false;
    const appearance = (card: Element) => {
      const box = getComputedStyle(card);
      const title = getComputedStyle(card.querySelector('.research-card-title')!);
      return {
        border: box.border, padding: box.padding, background: box.backgroundImage,
        accent: getComputedStyle(card, '::before').backgroundImage, font: title.font,
      };
    };
    const grid = getComputedStyle(list);
    return {
      first: appearance(cards[0]), second: appearance(cards[1]),
      columns: grid.gridTemplateColumns.split(' ').length, gap: grid.gap,
    };
  });
  const styles = await result.jsonValue();
  if (!styles) throw new Error(`Publication cards unavailable at ${path}`);
  return styles;
}

test('disease and collection cards match the Research Radar publication list', async ({ page }) => {
  test.setTimeout(180_000);
  const reference = await cardAppearance(page, '/research/');
  expect(reference.first).toEqual(reference.second);
  expect(reference.columns).toBe(page.viewportSize()!.width <= 900 ? 1 : 2);
  for (const path of ['/research/diseases/mumps/', '/fr/research/countries/us/']) {
    expect(await cardAppearance(page, path)).toEqual(reference);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  }
});
