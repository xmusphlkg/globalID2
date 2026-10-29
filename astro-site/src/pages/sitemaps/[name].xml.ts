import type { APIRoute } from 'astro';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  buildSitemapGroups,
  renderSitemapXml,
  SITEMAP_GROUPS,
  type SitemapGroup,
} from '../../lib/sitemap-entries';
import {
  diseaseIndex,
  loadCountry,
  loadDisease,
  loadKnowledge,
  loadReport,
  reportIndex,
  siteMeta,
} from '../../lib/seo-page-data';
import { listMonths, listWeeks, readSituationMap } from '../../utils/international';

const fallbackSite = 'https://globalinfectiousdisease.com';
const researchFile = resolve('./src/data/research/index.json');

/** Adapt the alert export to the sitemap's situation shape. */
function loadSituation() {
  const map = readSituationMap();
  const latest = map
    ? { public_enabled: true, generated_at: map.generated_at, report: { as_of: map.as_of } }
    : null;
  return {
    latest,
    weeks: listWeeks().map(week => ({ period_key: week.period_key, generated_at: map?.generated_at })),
    months: listMonths().map(period_key => ({ period_key })),
  };
}

export function getStaticPaths() {
  return SITEMAP_GROUPS.map(name => ({ params: { name } }));
}

export const GET: APIRoute = ({ params, site }) => {
  const name = params.name as SitemapGroup;
  if (!SITEMAP_GROUPS.includes(name)) return new Response('Not found', { status: 404 });
  const entries = buildSitemapGroups({
    meta: siteMeta,
    diseases: diseaseIndex,
    reports: reportIndex,
    loadReport,
    loadDisease,
    loadKnowledge,
    loadCountry,
    situation: loadSituation(),
    research: existsSync(researchFile) ? JSON.parse(readFileSync(researchFile, 'utf-8')) : undefined,
  })[name];
  return new Response(renderSitemapXml(entries, site?.toString() ?? fallbackSite), {
    headers: { 'Content-Type': 'application/xml; charset=utf-8' },
  });
};
