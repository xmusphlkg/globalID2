import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { readPublishableReports } from '../src/lib/report-data.ts';
import { reportOverviewTitle } from '../src/lib/report-seo.ts';

export function checkReportInputs(reports, loadCountry = () => null) {
  const titles = new Map();
  const errors = [];
  for (const { country, id, detail } of reports) {
    const countryData = loadCountry(country);
    for (const locale of ['en', 'zh', 'fr']) {
      const title = reportOverviewTitle(detail, country, locale, countryData).replace(/\s+/g, ' ').trim();
      const route = `/${locale}/countries/${country}/reports/${id}/`;
      const key = `${locale}:${title}`;
      if (!title) errors.push(`${route}: missing title`);
      if ([...title].length > 70) errors.push(`${route}: title exceeds 70 characters`);
      if (titles.has(key)) errors.push(`${route}: duplicate indexable title also used by ${titles.get(key)}`);
      else titles.set(key, route);
    }
  }
  return errors;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const reports = readPublishableReports();
  const errors = checkReportInputs(reports, country => {
    const path = resolve('src/data/countries', `${country}.json`);
    return existsSync(path) ? JSON.parse(readFileSync(path, 'utf8')) : null;
  });
  if (errors.length) {
    process.stderr.write(`${errors.join('\n')}\n`);
    process.exitCode = 1;
  } else process.stdout.write(`[report-inputs] checked ${reports.length} report overviews in 3 locales\n`);
}
