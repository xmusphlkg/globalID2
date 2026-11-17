import fs from 'node:fs';
import { performance } from 'node:perf_hooks';
import { defaultWindow, resultsFor, sharedCeiling } from '/home/likangguo/globalID/globalID2/astro-site/src/components/charts/subdivision-map/model.ts';
const root = '/home/likangguo/globalID/globalID2/astro-site/public/site-data/subdivision-maps/us/';
const catalog=JSON.parse(fs.readFileSync(root+'index.json'));
const populations=JSON.parse(fs.readFileSync(root+'population.json'));
const dataset=JSON.parse(fs.readFileSync(root+'d094.json'));
const window={from:'2025-01-01',to:'2025-12-31',metric:'rate',view:'monthly'};
for(let run=0; run<3; run++) {
 const start=performance.now();
 const ceiling=sharedCeiling(catalog,dataset,populations,window);
 console.log(JSON.stringify({run:run+1,operation:'sharedCeiling',country:'US',disease:dataset.disease_id,years:dataset.years.length,regions:catalog.regions.length,points:Object.values(dataset.regions).reduce((n,r)=>n+r.points.length,0),ceiling,ms:Math.round(performance.now()-start)}));
}
