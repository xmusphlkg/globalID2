import type { APIRoute } from 'astro';

import diseasesRaw from '../data/diseases/index.json';
import metaRaw from '../data/meta.json';
import reportsIndexRaw from '../data/reports/index.json';
import { loadReportDetail } from '../lib/report-data';
import {
  buildSitemapEntries,
  renderSitemapXml,
  type SitemapDisease,
  type SitemapMeta,
  type SitemapReport,
} from '../lib/sitemap-entries';

const fallbackSite = 'https://globalinfectiousdisease.com';
const meta = metaRaw as SitemapMeta;
const diseases = diseasesRaw as SitemapDisease[];
const reportsIndex = reportsIndexRaw as SitemapReport[];

export const GET: APIRoute = ({ site }) => {
  const siteUrl = new URL(site?.toString() ?? fallbackSite);
  const entries = buildSitemapEntries({
    meta,
    diseases,
    reports: reportsIndex,
    loadReport: loadReportDetail,
  });
  const xml = renderSitemapXml(entries, siteUrl);

  return new Response(xml, {
    headers: {
      'Content-Type': 'application/xml; charset=utf-8',
    },
  });
};
