import { chromium, expect } from '/home/likangguo/globalID/globalID2/astro-site/node_modules/@playwright/test/index.mjs';
import { writeFile } from 'node:fs/promises';
const browser = await chromium.launch({ headless: true }), results = [];
try {
 for (const width of [390,1280]) for (const lang of ['en','zh','fr']) {
  const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage();
  const requests=[];
  await page.route('**/site-data/situation/rss.xml*',route=>{requests.push(route.request().url());return route.fulfill({contentType:'application/rss+xml',body:'<?xml version="1.0"?><rss version="2.0"><channel><title>Fixture official reports</title><link>https://example.test</link><description>Fixture</description></channel></rss>'});});
  const path=`http://127.0.0.1:4322/${lang==='en'?'':lang+'/'}subscribe/`;
  for (const filter of ['country','disease']) {
   await page.goto(path,{timeout:90000});
   const rss=page.locator('.situation-rss');await rss.scrollIntoViewIfNeeded();
   await expect(rss.locator('a[type="application/rss+xml"]')).toHaveAttribute('href',`/site-data/situation/rss.xml?lang=${lang}`);
   const select=rss.locator(`select[name="${filter}"]`);
   const value=await select.locator('option').nth(1).getAttribute('value');expect(value).toBeTruthy();
   await select.selectOption(value);
   await select.locator('xpath=..').getByRole('button').click();
   await page.waitForURL('**/site-data/situation/rss.xml?*');
   const url=new URL(page.url());expect(url.searchParams.get('lang')).toBe(lang);expect(url.searchParams.get(filter)).toBe(value);
  }
  expect(requests).toHaveLength(2);results.push({width,lang,country_form:true,disease_form:true,all_feed_language:true});
  await context.close();
 }
}finally{await browser.close();await writeFile('/tmp/globalid-fixes-20261116/rss-ui-smoke.json',JSON.stringify(results,null,2));}
console.log(JSON.stringify(results));
