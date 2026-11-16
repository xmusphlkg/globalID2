import { chromium, expect } from '/home/likangguo/globalID/globalID2/astro-site/node_modules/@playwright/test/index.mjs';
import { readFile, writeFile } from 'node:fs/promises';
const meta = JSON.parse(await readFile('/home/likangguo/globalID/globalID2/astro-site/src/data/meta.json','utf8'));
const codes = 'ET KE UG GH TZ PH VN LA MM BN TL PY EC CR PA GP MQ GF'.split(' ');
const result = { mode: 'isolated already-built static dist; original local meta fixture only; no shared build or production request', combinations: [] };
const browser = await chromium.launch({headless:true});
try {
 for (const lang of ['en','zh','fr']) for (const width of [390,1280]) {
  const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage(),errors=[];
  page.on('pageerror', error=>errors.push(error.message));
  await page.route('**/site-data/bootstrap.json',r=>r.fulfill({status:404}));
  await page.route('**/site-data/current.json',r=>r.fulfill({status:404}));
  await page.route('**/site-data/meta.json',r=>r.fulfill({json:meta}));
  const prefix=lang==='en'?'':`/${lang}`;
  await page.goto(`http://127.0.0.1:4333${prefix}/countries/`,{timeout:60000});
  await page.locator('.coverage-filter').first().waitFor();
  const directory=page.locator('.coverage-filter').first().locator('xpath=../..');
  await directory.scrollIntoViewIfNeeded();
  const pending={en:'Data publication pending',zh:'数据待发布',fr:'Publication des données en attente'}[lang];
  for (const code of codes) {
   const card=directory.locator('.grid.gap-4 > div').filter({has:page.getByText(code,{exact:true})});
   await expect(card).toHaveCount(1);
   await expect(card).toContainText(pending);
   await expect(directory.locator(`a[href="${prefix}/countries/${code.toLowerCase()}/"]`)).toHaveCount(0);
  }
  for (const code of ['us-pr','us-vi']) await expect(directory.locator(`a[href="${prefix}/countries/${code}/"]`)).toHaveCount(1);
  const filters=directory.locator('.coverage-filter select');
  await filters.nth(0).selectOption('AMR');
  await filters.nth(2).selectOption('HIC');
  await expect(directory.getByText('CR',{exact:true})).toBeVisible();
  await expect(directory.getByText('GP',{exact:true})).toHaveCount(0);
  await filters.nth(3).selectOption('scheduled');
  await expect(directory.getByText('CR',{exact:true})).toBeVisible();
  await expect(directory.getByText('US-PR',{exact:true})).toHaveCount(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth-innerWidth)).toBeLessThanOrEqual(1);
  expect(errors).toEqual([]);
  result.combinations.push({lang,width,pending_codes:codes,no_unpublished_country_links:true,published_territories_merge:true,cr_amr_hic_filter:true,null_worldbank_not_forged:true,pending_filter:true,page_errors:errors});
  await context.close();
 }
 result.status='passed';
} catch (error) { result.status='failed';result.error=error.stack;process.exitCode=1; }
finally {await browser.close();await writeFile('/tmp/globalid-fixes-20261116/frontend-directory-integration.json',JSON.stringify(result,null,2)+'\n');}
console.log(JSON.stringify(result));
