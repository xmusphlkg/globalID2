// Read-only production capture. The response archive can replay the same data
// while comparing a local candidate without racing a live JSON publication.
import { chromium } from 'playwright';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { extname, resolve } from 'node:path';

const origin = process.env.GIDS_BASELINE_ORIGIN || 'https://globalinfectiousdisease.com';
const output = resolve(process.argv[2] || '../exports/production-browser-baseline');
const candidate = process.env.GIDS_BASELINE_CANDIDATE ? resolve(process.env.GIDS_BASELINE_CANDIDATE) : null;
const archive = process.env.GIDS_BASELINE_ARCHIVE ? resolve(process.env.GIDS_BASELINE_ARCHIVE) : null;
if (candidate && !archive) throw new Error('Candidate capture requires a frozen production response archive');
const archived = archive ? new Map(JSON.parse(readFileSync(resolve(archive, 'responses.json'))).map(row => [row.url, row])) : null;
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.webp': 'image/webp', '.woff2': 'font/woff2', '.ico': 'image/x-icon' };
const paths = ['/', '/countries/jp/', '/countries/cn/', '/diseases/influenza/', '/situation/', '/research/', '/downloads/'];
const jobs = ['en', 'zh', 'fr'].flatMap(locale => paths.flatMap(path => [390, 1280].map(width => ({
  locale, width, pathname: `${locale === 'en' ? '' : '/' + locale}${path}`,
})))).filter(job => (!process.env.GIDS_BASELINE_PATH_FILTER || job.pathname.includes(process.env.GIDS_BASELINE_PATH_FILTER))
  && (!process.env.GIDS_BASELINE_PATH_REGEX || new RegExp(process.env.GIDS_BASELINE_PATH_REGEX).test(job.pathname)));
const sha = body => createHash('sha256').update(body).digest('hex');
mkdirSync(resolve(output, 'responses'), { recursive: true });
const results = [], responses = new Map(), pending = new Set();
const started = new Date().toISOString();
const browser = await chromium.launch({ headless: true });
const persist = () => {
  writeFileSync(resolve(output, 'responses.json'), JSON.stringify([...responses.values()], null, 2));
  writeFileSync(resolve(output, 'baseline.json'), JSON.stringify({ started, observed_at: new Date().toISOString(),
    origin, candidate, archive, scope: candidate ? 'Local candidate with frozen production responses; no external traffic' : 'Read-only public production browser baseline; no publication or settings writes',
    expected_cases: jobs.length, cases: results.length, results }, null, 2));
};
let next = 0;
async function worker() {
  while (next < jobs.length) {
    const job = jobs[next++], { pathname, locale, width } = job;
    const name = `${pathname.replace(/^\/+|\/+$/g, '').replaceAll('/', '-') || 'home'}-${width}`;
    const context = await browser.newContext({ viewport: { width, height: 900 }, acceptDownloads: true });
    if (candidate) await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      const lookup = url.href;
      if (new URL(lookup).pathname.startsWith('/site-data/')) {
        const frozen = archived.get(lookup);
        if (!frozen) return route.fulfill({ status: 404, body: 'Missing frozen production JSON' });
        return route.fulfill({ status: frozen.status, body: readFileSync(resolve(archive, 'responses', frozen.sha256)),
          headers: { 'content-type': frozen.content_type || 'application/json', 'access-control-allow-origin': origin } });
      }
      if (url.origin === origin) {
        let path = resolve(candidate, '.' + decodeURIComponent(url.pathname));
        if ((path === candidate || path.startsWith(candidate + '/')) && existsSync(path)) {
          if (statSync(path).isDirectory()) path = resolve(path, 'index.html');
          if (existsSync(path) && statSync(path).isFile()) return route.fulfill({ body: readFileSync(path), contentType: mime[extname(path)] || 'application/octet-stream' });
        }
      }
      const frozen = archived.get(url.href);
      if (frozen) return route.fulfill({ status: frozen.status, body: readFileSync(resolve(archive, 'responses', frozen.sha256)), contentType: frozen.content_type });
      return route.abort();
    });
    const page = await context.newPage(), pageErrors = [], failures = [], httpErrors = [];
    page.on('pageerror', error => pageErrors.push(error.stack || error.message));
    page.on('requestfailed', req => failures.push({ url: req.url(), error: req.failure()?.errorText }));
    page.on('response', response => {
      const request = response.request(), url = response.url();
      if (response.status() >= 400) httpErrors.push({ url, status: response.status() });
      if (request.method() !== 'GET' || !new URL(url).hostname.endsWith('globalinfectiousdisease.com') || url.includes('/cdn-cgi/')) return;
      const promise = (async () => {
        try {
          const body = await response.body(), digest = sha(body);
          writeFileSync(resolve(output, 'responses', digest), body);
          const entry = { url, status: response.status(), sha256: digest, bytes: body.length,
            content_type: response.headers()['content-type'], cache_control: response.headers()['cache-control'] };
          responses.set(url + '\0' + digest, entry);
        } catch { /* Navigation can cancel nonessential requests; failures are recorded above. */ }
      })();
      pending.add(promise); promise.finally(() => pending.delete(promise));
    });
    let result = { ...job, name };
    try {
      const navigation = await page.goto(origin + pathname, { waitUntil: 'domcontentloaded', timeout: 60000 });
      result.status = navigation.status();
      await page.locator('h1').first().waitFor({ state: 'visible', timeout: 30000 });
      await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {});
      // Hydration can extend the page while scrolling. Re-read the height and
      // make a second pass so every lazy island observes the viewport.
      for (let pass = 0; pass < 2; pass++) for (let y = 0; y < Math.min(await page.evaluate(() => document.documentElement.scrollHeight), 60000); y += 850) {
        await page.evaluate(y => scrollTo(0, y), y); await page.waitForTimeout(100);
      }
      if (await page.locator('footer').last().isVisible()) await page.locator('footer').last().scrollIntoViewIfNeeded();
      await page.waitForFunction(() => [...document.querySelectorAll('[data-gids-loading]')]
        .every(node => !node.getClientRects().length || !node.getBoundingClientRect().width), null, { timeout: 15000 });
      await page.waitForFunction(() => [...document.querySelectorAll('[data-site-updated], [data-database-updated]')]
        .every(node => !node.getClientRects().length || !['Loading', '读取中', 'Chargement'].includes(node.textContent.trim())), null, { timeout: 15000 });
      await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
      await page.evaluate(() => scrollTo(0, 0));
      await page.waitForTimeout(300);
      result = { ...result, ...await page.evaluate(() => {
        const css = element => { const s = getComputedStyle(element); const r = element.getBoundingClientRect();
          return { color: s.color, background: s.backgroundColor, font: s.fontFamily, fontSize: s.fontSize,
            fontWeight: s.fontWeight, lineHeight: s.lineHeight, padding: s.padding,
            borderRadius: s.borderRadius, x: r.x, y: r.y, width: r.width, height: r.height }; };
        return { title: document.title, lang: document.documentElement.lang, release_id: document.documentElement.dataset.releaseId,
          canonical: document.querySelector('link[rel="canonical"]')?.href,
          hreflang: [...document.querySelectorAll('link[hreflang]')].map(e => ({ lang: e.hreflang, href: e.href })),
          description: document.querySelector('meta[name="description"]')?.content,
          structured_data: [...document.querySelectorAll('script[type="application/ld+json"]')].map(e => e.textContent),
          h1: [...document.querySelectorAll('h1')].map(e => e.textContent.trim()), text: document.body.innerText,
          document_width: document.documentElement.scrollWidth, document_height: document.documentElement.scrollHeight,
          links: [...document.querySelectorAll('a[href]')].filter(e => !e.href.includes('/cdn-cgi/')).map(e => ({
            text: e.innerText.trim(), href: e.getAttribute('href'), download: e.getAttribute('download') })),
          controls: [...document.querySelectorAll('button,select,input')].map(e => ({ tag: e.tagName, text: e.innerText,
            type: e.type, label: e.getAttribute('aria-label'), disabled: e.disabled })),
          charts: [...document.querySelectorAll('canvas')].map(e => ({ width: e.width, height: e.height })),
          styles: ['body', 'header', 'main', 'h1', 'footer', '.site-container'].map(selector => ({ selector,
            value: document.querySelector(selector) ? css(document.querySelector(selector)) : null })),
        };
      }) };
      result.screenshot = `${name}.png`;
      await page.screenshot({ path: resolve(output, result.screenshot), fullPage: false });
      const target = locale === 'fr' ? 'en' : 'fr';
      const navigated = page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 60000 });
      if (width < 1024 && await page.locator('#mobile-menu-toggle').count()) {
        await page.locator('#mobile-menu-toggle').click();
        await page.locator(`.site-mobile-language-options a[hreflang^="${target}"]`).click();
      } else {
        await page.locator('.site-language-selector > summary').click();
        await page.locator(`.site-language-option[hreflang^="${target}"]`).click();
      }
      const languageResponse = await navigated;
      result.language_switch_status = languageResponse?.status();
      await page.waitForFunction(expected => document.documentElement.lang.startsWith(expected), target);
      await page.locator('h1').first().waitFor({ state: 'visible' });
      await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
      result.language_switch_target = page.url();
      result.captured = true;
    } catch (error) { result.error = String(error); result.captured = false; }
    result.page_errors = pageErrors; result.request_failures = failures; result.http_errors = httpErrors;
    results.push(result); persist();
    console.log(JSON.stringify({ name, captured: result.captured, status: result.status,
      charts: result.charts?.length, errors: pageErrors.length, http_errors: httpErrors.length }));
    await Promise.allSettled([...pending]);
    await context.close();
  }
}
const concurrency = Number(process.env.GIDS_BROWSER_CAPTURE_WORKERS || 3);
if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 3) throw new Error('Capture workers must be 1–3');
try { await Promise.all(Array.from({ length: concurrency }, () => worker())); }
finally { await Promise.allSettled([...pending]); await browser.close(); persist(); }
console.log(JSON.stringify({ output, cases: results.length, captured: results.filter(x => x.captured).length, responses: responses.size }));
