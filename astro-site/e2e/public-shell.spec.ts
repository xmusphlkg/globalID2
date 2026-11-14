import { expect, test } from '@playwright/test';

test('all public layouts share navigation and footer, with the map footer hidden', async ({ page }) => {
  test.setTimeout(120_000);
  for (const prefix of ['', '/zh', '/fr']) {
    let expectedFooter: string[] | undefined;
    const expectedNav = ['situation', 'countries', 'diseases', 'reports', 'compare', 'downloads', 'research', 'about'].map(path => `${prefix}/${path}/`);
    for (const path of ['/', '/countries/', '/research/', '/reports/', '/changelog/', '/data-updates/', '/situation/']) {
      await page.goto(`${prefix}${path}`);
      const header = page.locator('header.site-header');
      await expect(header).toHaveCount(1);
      for (const selector of ['.site-nav-desktop a', '#mobile-menu a.nav-link']) {
        const links = await header.locator(selector).evaluateAll(elements => elements.map(element => new URL((element as HTMLAnchorElement).href).pathname));
        expect(links, `${prefix}${path} ${selector}`).toEqual(expectedNav);
      }
      const footer = page.locator('footer.site-footer');
      await expect(footer).toHaveCount(path === '/situation/' ? 0 : 1);
      if (path === '/situation/') continue;
      const labels = await footer.locator('a').allTextContents();
      expectedFooter ??= labels;
      expect(labels).toEqual(expectedFooter);
      expect(labels.some(label => /v\d+\.\d+\.\d+/.test(label))).toBe(false);
    }
  }
});
