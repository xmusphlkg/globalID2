import type { APIRoute } from 'astro';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  buildSitemapGroups,
  renderSitemapXml,
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
import { REPORTS_ORIGIN } from '../../lib/papers';

const researchFile = resolve('./src/data/research/index.json');

export const GET: APIRoute = () => {
  const generated = buildSitemapGroups({
    meta: siteMeta,
    diseases: diseaseIndex,
    reports: reportIndex,
    loadReport,
    loadDisease,
    loadKnowledge,
    loadCountry,
    research: existsSync(researchFile) ? JSON.parse(readFileSync(researchFile, 'utf-8')) : undefined,
  }).reports;
  return new Response(renderSitemapXml(generated, REPORTS_ORIGIN), {
    headers: { 'Content-Type': 'application/xml; charset=utf-8' },
  });
};
