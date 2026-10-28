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
import { listDigestMonths } from '../../utils/international';

const fallbackSite = 'https://globalinfectiousdisease.com';
const situationDirectory = resolve('./src/data/situation');
const researchFile = resolve('./src/data/research/index.json');

/** Adapt the international notice export to the sitemap's situation shape. */
function loadSituation() {
  const base = resolve(situationDirectory, 'international');
  const latestFile = resolve(base, 'latest.json');
  const raw = existsSync(latestFile) ? JSON.parse(readFileSync(latestFile, 'utf-8')) : null;
  const latest = raw
    ? { public_enabled: true, generated_at: raw.generated_at, report: { as_of: raw.window?.as_of } }
    : null;
  const digestDir = resolve(base, 'digests');
  const weeks = existsSync(digestDir)
    ? readdirSync(digestDir)
      .filter(name => /^\d{4}-W\d{2}\.json$/.test(name))
      .map(name => JSON.parse(readFileSync(resolve(digestDir, name), 'utf-8')))
      .map(row => ({ period_key: row.period_key, generated_at: row.generated_at }))
    : [];
  return { latest, weeks, months: listDigestMonths().map(period_key => ({ period_key })) };
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
