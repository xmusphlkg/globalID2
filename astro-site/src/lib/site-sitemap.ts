import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { buildSitemapGroups } from './sitemap-entries';
import { diseaseIndex, loadCountryDirectory, loadDiseaseDirectory, loadCountry, loadDisease, loadKnowledge, loadReport, reportIndex, siteMeta } from './seo-page-data';
import { listMonths, listWeeks, readSituationMap } from '../utils/international';
import { jsonFileCache } from './json-file-cache';
import { memoizeForBuild } from './build-memo';

// Every sitemap partition and the papers host share one derived catalogue.
// Previously each endpoint recomputed every group, including full surveillance
// ranking and research facet histories, before discarding all other groups.
export const siteSitemapGroups = memoizeForBuild(() => {
  const map = readSituationMap();
  const researchFile = resolve('./src/data/research/index.json');
  return buildSitemapGroups({
    meta: siteMeta, diseases: diseaseIndex, reports: reportIndex,
    loadReport, loadDisease, loadKnowledge, loadCountry, loadCountryDirectory, loadDiseaseDirectory,
    situation: {
      latest: map ? { public_enabled: true, generated_at: map.generated_at, report: { as_of: map.as_of } } : null,
      weeks: listWeeks().map(week => ({ period_key: week.period_key, generated_at: map?.generated_at })),
      months: listMonths().map(period_key => ({ period_key })),
    },
    research: existsSync(researchFile) ? jsonFileCache.read(researchFile) : undefined,
  });
});
