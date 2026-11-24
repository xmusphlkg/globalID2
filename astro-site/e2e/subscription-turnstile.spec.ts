import { expect, test, type Page } from '@playwright/test';

const sdk = `
window.__turnstileWidgets = {};
window.__turnstileSerial = 0;
window.turnstile = {
  render(target, options) {
    const id = 'widget-' + Object.keys(window.__turnstileWidgets).length;
    const button = document.createElement('button');
    target.style.minWidth = options.size === 'compact' ? '150px' : '300px';
    button.type = 'button'; button.dataset.testVerify = 'true'; button.textContent = 'Verify test challenge';
    button.onclick = () => options.callback('fresh-token-' + (++window.__turnstileSerial));
    target.append(button);
    window.__turnstileWidgets[id] = { options, target };
    return id;
  },
  reset(id) { window.__turnstileWidgets[id].options['expired-callback'](); },
  remove(id) { window.__turnstileWidgets[id].target.replaceChildren(); }
};`;

async function setup(page: Page, scriptFailsOnce = false) {
  let scriptCalls = 0;
  await page.route('https://challenges.cloudflare.com/turnstile/v0/api.js*', route => {
    if (scriptFailsOnce && scriptCalls++ === 0) return route.abort();
    return route.fulfill({ contentType: 'application/javascript', body: sdk });
  });
  await page.route(/(\/__dev\/subscription-options|\/api\/subscriptions\/options)$/, route => route.fulfill({
    json: { ok: true, turnstile: { enabled: true, sitekey: 'test-public-sitekey' },
      locales: [{ value: 'en', label_en: 'English' }],
      lists: [{ code: 'reports', name_en: 'Reports' }], filters: {} },
    headers: { 'access-control-allow-origin': '*' },
  }));
  const tokens: string[] = [];
  await page.route('https://subscriptions.globalinfectiousdisease.com/api/subscriptions', route => {
    if (route.request().method() === 'OPTIONS') return route.fulfill({ status: 204, headers: {
      'access-control-allow-origin': '*', 'access-control-allow-methods': 'POST, OPTIONS',
      'access-control-allow-headers': 'content-type',
    } });
    const payload = route.request().postDataJSON();
    tokens.push(payload.turnstileToken);
    return route.fulfill({
      status: tokens.length === 1 ? 403 : 201,
      json: tokens.length === 1 ? { error: 'turnstile_verification_failed' } : { ok: true, email: { status: 'pending' } },
      headers: { 'access-control-allow-origin': '*' },
    });
  });
  return tokens;
}

for (const [prefix, language, required] of [
  ['', 'en', 'Complete the security verification'],
  ['/zh', 'zh-cn', '请先完成人机验证'],
  ['/fr', 'fr', 'Terminez la vérification'],
]) {
  test(`Turnstile preserves input, rejects missing/expired tokens and refreshes resend tokens (${language})`, async ({ page }) => {
    const tokens = await setup(page);
    await page.goto(`${prefix}/subscribe/`);
    await expect(page.locator('#subscription-turnstile [data-test-verify]')).toBeVisible();
    const layout = await page.locator('#subscription-turnstile').evaluate(element => ({ width: element.clientWidth, scroll: element.scrollWidth }));
    expect(layout.scroll).toBeLessThanOrEqual(layout.width);
    await page.locator('#subscriber-email').fill('reader@example.test');
    await page.locator('input[name="consent"]').check();
    await page.locator('#subscription-submit').click();
    await expect(page.locator('#subscription-status')).toContainText(required);
    expect(tokens).toEqual([]);
    await page.locator('#subscription-turnstile [data-test-verify]').click();
    await page.locator('#subscription-submit').click();
    await expect.poll(() => tokens.length).toBe(1);
    await expect(page.locator('#subscriber-email')).toHaveValue('reader@example.test');
    await expect(page.locator('input[name="consent"]')).toBeChecked();
    await expect(page.locator('#subscription-modal')).toBeHidden();
    // A token that expires while the user edits cannot be submitted.
    await page.locator('#subscription-turnstile [data-test-verify]').click();
    await page.evaluate("window.__turnstileWidgets['widget-0'].options['expired-callback']()");
    await page.locator('#subscription-submit').click();
    await expect(page.locator('#subscription-status')).toContainText(required);
    expect(tokens).toHaveLength(1);
    await page.locator('#subscription-turnstile [data-test-verify]').click();
    await page.locator('#subscription-submit').click();
    await expect(page.locator('#subscription-modal')).toBeVisible();
    await page.locator('#subscription-resend').click();
    await expect(page.locator('#subscription-resend-status')).toContainText(required);
    expect(tokens).toHaveLength(2);
    await page.locator('#subscription-resend-turnstile [data-test-verify]').click();
    await page.locator('#subscription-resend').click();
    await expect.poll(() => tokens.length).toBe(3);
    expect(new Set(tokens).size).toBe(3);
    expect(tokens.every(token => typeof token === 'string' && token.startsWith('fresh-token-'))).toBe(true);
    const options = await page.evaluate("Object.values(window.__turnstileWidgets).map(widget => ({ action: widget.options.action, language: widget.options.language }))");
    expect(options).toEqual([{ action: 'subscribe', language }, { action: 'subscribe', language }]);
  });
}

test('a blocked Turnstile script can be retried without losing entered fields', async ({ page }) => {
  const tokens = await setup(page, true);
  await page.goto('/zh/subscribe/');
  await page.locator('#subscriber-email').fill('reader@example.test');
  const retry = page.locator('#subscription-turnstile').getByRole('button', { name: '重试验证', exact: true });
  await expect(retry).toBeVisible();
  await retry.click();
  await expect(page.locator('#subscription-turnstile [data-test-verify]')).toBeVisible();
  await expect(page.locator('#subscriber-email')).toHaveValue('reader@example.test');
  expect(tokens).toEqual([]);
});
