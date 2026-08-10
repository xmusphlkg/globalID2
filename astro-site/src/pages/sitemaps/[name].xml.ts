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

function loadSituation() {
  const latestFile = resolve(situationDirectory, 'latest.json');
  const latest = existsSync(latestFile) ? JSON.parse(readFileSync(latestFile, 'utf-8')) : null;
  const archiveDirectory = resolve(situationDirectory, 'archive');
  const archives = existsSync(archiveDirectory)
    ? readdirSync(archiveDirectory)
      .filter(name => /^\d{4}-W\d{2}\.json$/.test(name))
      .map(name => JSON.parse(readFileSync(resolve(archiveDirectory, name), 'utf-8')))
    : [];
  return { latest, archives };
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
  })[name];
  return new Response(renderSitemapXml(entries, site?.toString() ?? fallbackSite), {
    headers: { 'Content-Type': 'application/xml; charset=utf-8' },
  });
};
