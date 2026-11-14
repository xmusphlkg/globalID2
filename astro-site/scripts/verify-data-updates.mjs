// Live feeds only: no mocked responses, no subscription submissions.
import { chromium, expect } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const repo=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const evidence=process.env.LIVE_UI_OUTPUT || path.join(repo, 'exports/data-updates-acceptance', new Date().toISOString().replace(/[:.]/g, '-'));
await fs.mkdir(evidence, { recursive: true });
const origin=(process.env.LIVE_UI_ORIGIN||'https://globalinfectiousdisease.com').replace(/\/$/,'');
const today=new Date();
const from=process.env.DATA_UPDATES_FROM || new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth()-1, 1)).toISOString().slice(0,10);
const through=process.env.DATA_UPDATES_THROUGH || today.toISOString().slice(0,10);
const copy={
 en:{title:'Data & website updates',from:'From (UTC)',through:'Through (UTC)',type:'Update type',trend:'Publication records by day',channels:['Surveillance','Situation','Research','Knowledge','Website']},
 zh:{title:'网站与数据更新',from:'起始日期（UTC）',through:'截止日期（UTC）',type:'更新类型',trend:'每日发布记录',channels:['监测数据','疫情通报','论文数据','疾病知识库','网站版本']},
 fr:{title:'Mises à jour du site et des données',from:'Depuis (UTC)',through:'Jusqu’au (UTC)',type:'Type de mise à jour',trend:'Publications par jour',channels:['Surveillance','Situation','Recherche','Connaissances','Site web']},
};
const channels=['surveillance','situation','literature','knowledge','website'];
const browser=await chromium.launch({headless:true});
const results=[];
let activeCase=null;
let checkpoint='launch';
try {
 for(const locale of ['en','zh','fr'])for(const width of [360,1280]){
  const page=await browser.newPage({viewport:{width,height:960}});
  await page.route('**/*', route => ['GET','HEAD','OPTIONS'].includes(route.request().method()) ? route.continue() : route.abort());
  activeCase={locale,width};checkpoint='navigation';
  const archives=new Map(), reads=[], errors=[], updateScripts=[], readErrors=[];
  let latest=null, changelog=null;
  page.on('pageerror',error=>errors.push(error.message));
  page.on('response',response=>{
   const url=new URL(response.url());
   if(/DataUpdates\..*\.js$/.test(url.pathname))updateScripts.push({url:response.url(),status:response.status()});
   if(/^\/site-data\/updates\/(?:latest|changelog|\d{4}-\d{2})\.json$/.test(url.pathname)&&response.ok()){
    reads.push(response.json().then(body=>{
     if(url.pathname.endsWith('/latest.json'))latest=body;
     else if(url.pathname.endsWith('/changelog.json'))changelog=body;
     else archives.set(body.month,body.events);
    }).catch(error=>readErrors.push({url:response.url(),message:error.message})));
   }
  });
  const navigation=await page.goto(origin+(locale==='en'?'':`/${locale}`)+'/data-updates/',{waitUntil:'domcontentloaded'});
  expect(navigation.ok()).toBe(true);
  checkpoint='localized new UI';
  await expect(page.getByRole('heading',{name:copy[locale].title,level:1,exact:true})).toBeVisible();
  await expect(page.locator('input[type=date]')).toHaveCount(2,{timeout:15000});
  await expect(page.getByLabel(copy[locale].from,{exact:true})).toBeVisible();
  await expect(page.getByLabel(copy[locale].through,{exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:copy[locale].trend,exact:true})).toBeVisible();
  const select=page.getByRole('combobox',{name:copy[locale].type,exact:true});
  for(let index=0;index<channels.length;index++)await expect(select.locator(`option[value="${channels[index]}"]`)).toHaveText(copy[locale].channels[index]);
  await expect(page.locator('.updates-trend-chart')).toBeVisible({timeout:30000});
  checkpoint='date range and feeds';
  await page.getByLabel(copy[locale].from,{exact:true}).fill(from);
  await page.getByLabel(copy[locale].through,{exact:true}).fill(through);
  await expect(page.locator('.updates-status')).toBeEmpty({timeout:30000});
  await expect.poll(()=>latest?.schema_version===1&&Array.isArray(latest.months)&&changelog?.schema_version===1&&Array.isArray(changelog.releases),{timeout:30000}).toBe(true);
  const requiredMonths=latest.months.filter(month=>month>=from.slice(0,7)&&month<=through.slice(0,7));
  await expect.poll(()=>requiredMonths.every(month=>archives.has(month)),{timeout:30000}).toBe(true);
  await Promise.all(reads);
  const events=requiredMonths.flatMap(month=>archives.get(month)).filter(e=>{const day=new Date(e.published_at).toISOString().slice(0,10);return day>=from&&day<=through;});
  const expectedVersions=changelog.releases.filter(release=>release.date>=from&&release.date<=through);
  const number=async locator=>Number((await locator.textContent()).replace(/\D/g,''));
  await expect.poll(()=>number(page.locator('.updates-trend-counts dd').nth(0))).toBe(events.length);
  await expect.poll(()=>number(page.locator('.updates-trend-counts dd').nth(1))).toBe(expectedVersions.length);
  const websiteVersions=await number(page.locator('.updates-trend-counts dd').nth(1));
  await expect(page.locator('.updates-trend-note,.updates-metrics-note')).toHaveCount(0);
  checkpoint='daily chart';
  await page.locator('.updates-trend-table summary').click();
  const daily=await page.locator('.updates-trend-table tbody tr').evaluateAll(rows=>Object.fromEntries(rows.map(row=>[row.querySelector('th').textContent,Number(row.querySelector('td').textContent.replace(/\D/g,''))])));
  const versionDays=await page.locator('.updates-marker-website').evaluateAll(markers=>markers.map(marker=>marker.closest('li').querySelector('time').getAttribute('datetime')));
  expect(versionDays.sort()).toEqual(expectedVersions.map(release=>release.date).sort());
  const expectedDaily={};
  for(let day=new Date(`${from}T00:00:00Z`);day.toISOString().slice(0,10)<=through;day.setUTCDate(day.getUTCDate()+1))expectedDaily[day.toISOString().slice(0,10)]=0;
  for(const day of [...events.map(event=>new Date(event.published_at).toISOString().slice(0,10)),...versionDays])expectedDaily[day]++;
  expect(daily).toEqual(expectedDaily);
  const plottedTotal=Object.values(daily).reduce((a,b)=>a+b,0);
  expect(plottedTotal).toBe(events.length+websiteVersions);
  checkpoint='channel filters';
  const byChannel={};
  for(const channel of channels){
   await select.selectOption(channel);
   const expected=channel==='website'?websiteVersions:events.filter(e=>e.channel===channel).length;
   await expect.poll(()=>number(page.locator('.updates-trend-counts dd').nth(channel==='website'?1:0))).toBe(expected);
   await expect.poll(()=>number(page.locator('.updates-trend-counts dd').nth(channel==='website'?0:1))).toBe(0);
   await expect.poll(()=>page.locator(`.updates-marker-${channel}`).count()).toBe(expected);
   await expect(page.locator('.updates-list li')).toHaveCount(expected);
   const actual=await page.locator(`.updates-marker-${channel}`).count();
   expect(actual).toBe(expected);byChannel[channel]=actual;
  }
  await select.selectOption('all');
  await expect.poll(()=>number(page.locator('.updates-trend-counts dd').nth(0))).toBe(events.length);
  await page.locator('.updates-trend-table summary').click();
  checkpoint='responsive layout and screenshots';
  await expect.poll(()=>page.locator('.updates-trend-chart svg').evaluate(svg=>Math.abs(Number(svg.getAttribute('viewBox').split(' ')[2])-svg.getBoundingClientRect().width)<2)).toBe(true);
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
  expect(overflow).toBe(false);expect(errors).toEqual([]);
  await page.locator('.updates-trend').screenshot({path:path.join(evidence,`live-trend-${locale}-${width}.png`)});
  await page.evaluate(()=>scrollTo(0,0));
  await page.screenshot({path:path.join(evidence,`live-page-${locale}-${width}.png`)});
  results.push({locale,width,url:page.url(),range:{from,through},requiredMonths,dataRecords:events.length,websiteVersions,plottedTotal,byChannel,updateScripts,readErrors,errors,horizontalOverflow:overflow,checkedAt:new Date().toISOString()});
  await page.close();
 }
 const request=await browser.newContext();
 checkpoint='release identity';
 const response=await request.request.get(origin+'/release.json');
 expect(response.ok()).toBe(true);
 const release=await response.json();
 expect(typeof release.release_id).toBe('string');
 await fs.writeFile(path.join(evidence,'live-ui-verification.json'),JSON.stringify({results,release},null,2));
 console.log(JSON.stringify({results,release},null,2));
}catch(error){
 await fs.writeFile(path.join(evidence,'live-ui-verification-failure.json'),JSON.stringify({activeCase,checkpoint,results,error:String(error),checkedAt:new Date().toISOString()},null,2));
 throw error;
}finally{await browser.close();}
