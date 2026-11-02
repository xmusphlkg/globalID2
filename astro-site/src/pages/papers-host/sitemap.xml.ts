import type { APIRoute } from 'astro';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import {
  buildSitemapGroups,
  PAPERS_SITEMAP_GROUP,
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
import { PAPERS_ORIGIN } from '../../lib/papers';

// Emitted to dist/papers-host/ and moved to the root of the papers host by the
// release split step. Article pages live only there, so this is their sitemap.
const researchFile = resolve('./src/data/research/index.json');
// Sitemap protocol limit; exceeding it needs a sitemap index.
const MAX_URLS = 50_000;

export const GET: APIRoute = () => {
  const entries = buildSitemapGroups({
    meta: siteMeta,
    diseases: diseaseIndex,
    reports: reportIndex,
    loadReport,
    loadDisease,
    loadKnowledge,
    loadCountry,
    research: existsSync(researchFile) ? JSON.parse(readFileSync(researchFile, 'utf-8')) : undefined,
  })[PAPERS_SITEMAP_GROUP];
  if (entries.length > MAX_URLS) {
    throw new Error(`Papers sitemap has ${entries.length} URLs; split it into a sitemap index (limit ${MAX_URLS}).`);
  }
  return new Response(renderSitemapXml(entries, PAPERS_ORIGIN), {
    headers: { 'Content-Type': 'application/xml; charset=utf-8' },
  });
};
