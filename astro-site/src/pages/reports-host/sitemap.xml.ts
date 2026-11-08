import type { APIRoute } from 'astro';
import {
  buildReportsSitemapEntries,
  renderSitemapXml,
} from '../../lib/sitemap-entries';
import {
  loadReport,
  reportIndex,
  siteMeta,
} from '../../lib/seo-page-data';
import { REPORTS_ORIGIN } from '../../lib/papers';

export const GET: APIRoute = () => {
  const generated = buildReportsSitemapEntries({
    meta: siteMeta,
    reports: reportIndex,
    loadReport,
  });
  return new Response(renderSitemapXml(generated, REPORTS_ORIGIN), {
    headers: { 'Content-Type': 'application/xml; charset=utf-8' },
  });
};
