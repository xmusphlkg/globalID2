import type { APIRoute } from 'astro';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
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

const fallbackSite = 'https://globalinfectiousdisease.com';
const situationDirectory = resolve('./src/data/situation');
const researchFile = resolve('./src/data/research/index.json');

function loadSituation() {
  const latestFile = resolve(situationDirectory, 'v3', 'latest.json');
  const latest = existsSync(latestFile) ? JSON.parse(readFileSync(latestFile, 'utf-8')) : null;
  const loadDirectory = (directoryName: string, pattern: RegExp) => {
    const directory = resolve(situationDirectory, 'v3', directoryName);
    return existsSync(directory)
      ? readdirSync(directory)
        .filter(name => pattern.test(name))
        .map(name => JSON.parse(readFileSync(resolve(directory, name), 'utf-8')))
      : [];
  };
  return {
    latest,
    weeks: loadDirectory('weekly', /^\d{4}-W\d{2}\.json$/),
    months: loadDirectory('monthly', /^\d{4}-\d{2}\.json$/),
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
