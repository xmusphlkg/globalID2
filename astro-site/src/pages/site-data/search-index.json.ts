import type { APIRoute } from 'astro';
import researchRaw from '../../data/research/index.json';
import { diseaseIndex, indexableDiseases, publishableReports, siteMeta } from '../../lib/seo-page-data';

export const prerender = true;

type SearchEntry = {
  id: string;
  kind: 'country' | 'disease' | 'situation' | 'report' | 'research' | 'page';
  href: { en: string; zh?: string };
  title: { en: string; zh?: string };
  summary?: { en?: string; zh?: string };
  aliases: string[];
  updated_at?: string;
};

const localized = (path: string) => ({ en: path, zh: path === '/' ? '/zh/' : `/zh${path}` });
const clean = (values: unknown[]) => [...new Set(values.filter((value): value is string => typeof value === 'string' && value.trim().length > 0).map(value => value.trim()))];

export const GET: APIRoute = () => {
  const entries: SearchEntry[] = [];
  for (const country of siteMeta.countries ?? []) {
    const code = String(country.code ?? '').toLowerCase();
    if (!code || country.data_available === false) continue;
    entries.push({
      id: `country:${code}`,
      kind: 'country',
      href: localized(`/countries/${code}/`),
      title: { en: country.name_en ?? country.name ?? code.toUpperCase(), zh: country.name_zh },
      summary: { en: `${country.disease_count ?? 0} monitored diseases · official-source coverage`, zh: `${country.disease_count ?? 0} 种疾病 · 官方来源覆盖` },
      aliases: clean([country.name, country.name_en, country.name_zh, country.code]),
      updated_at: country.date_range?.end,
    });
  }

  const indexableIds = new Set(indexableDiseases().map(disease => disease.disease_id));
  for (const disease of diseaseIndex) {
    if (!indexableIds.has(disease.disease_id)) continue;
    const slug = String(disease.slug ?? '').toLowerCase();
    if (!slug) continue;
    entries.push({
      id: `disease:${disease.disease_id}`,
      kind: 'disease',
      href: localized(`/diseases/${slug}/`),
      title: { en: disease.name_en ?? slug, zh: disease.name_zh },
      summary: { en: disease.description ?? `${disease.category ?? ''} disease surveillance profile`, zh: `${disease.name_zh ?? disease.name_en}监测数据、趋势与证据说明` },
      aliases: clean([disease.disease_id, disease.slug, disease.name_en, disease.name_zh, disease.icd_10, disease.icd_11, disease.category]),
    });
  }

  for (const report of publishableReports()) {
    const detail: any = report.detail;
    const document = detail.report_document_v4 ?? detail.metadata?.report_document_v4 ?? detail;
    const title = document.title ?? {};
    const summary = document.summary ?? {};
    const path = `/countries/${report.country}/reports/${report.id}/`;
    entries.push({
      id: `report:${report.country}:${report.id}`,
      kind: 'report',
      href: localized(path),
      title: { en: title.en ?? `Surveillance report ${report.id}`, zh: title.zh ?? detail.title },
      summary: { en: summary.en, zh: summary.zh ?? detail.summary },
      aliases: clean([report.id, report.country, detail.country_name, detail.country_name_en, detail.period_start, detail.period_end]),
      updated_at: detail.created_at,
    });
  }

  const research: any = researchRaw;
  for (const article of [...(research.articles ?? []), ...(research.preprints ?? [])]) {
    if (article.indexable === false || !article.slug) continue;
    const summaryEn = article.why_it_matters_en ?? article.summary?.en?.gids_interpretation ?? article.summary?.en?.main_findings;
    const summaryZh = article.why_it_matters_zh ?? article.summary?.zh?.gids_interpretation ?? article.summary?.zh?.main_findings;
    entries.push({
      id: `research:${article.article_id ?? article.slug}`,
      kind: 'research',
      href: localized(`/research/articles/${article.slug}/`),
      title: { en: article.title, zh: article.title_zh },
      summary: { en: summaryEn, zh: summaryZh },
      aliases: clean([article.doi, article.pmid, article.journal, article.study_type, ...(article.diseases ?? []).flatMap((item: any) => [item.name_en, item.name_zh]), ...(article.countries ?? []).flatMap((item: any) => [item.code, item.name_en, item.name_zh])]),
      updated_at: article.updated_at ?? article.published_at,
    });
  }

  entries.push(
    { id: 'situation:latest', kind: 'situation', href: localized('/situation/'), title: { en: 'Current global situation', zh: '当前全球态势' }, summary: { en: 'Review priorities, official events, coverage, and methods.', zh: '复核优先级、官方事件、覆盖范围和方法。' }, aliases: ['situation', 'signals', 'events', '态势', '信号', '事件'] },
    { id: 'page:countries', kind: 'page', href: localized('/countries/'), title: { en: 'Country data directory', zh: '国家与地区数据目录' }, aliases: ['countries', 'regions', '国家', '地区'] },
    { id: 'page:diseases', kind: 'page', href: localized('/diseases/'), title: { en: 'Disease directory', zh: '疾病目录' }, aliases: ['diseases', 'conditions', '疾病'] },
    { id: 'page:downloads', kind: 'page', href: localized('/downloads/'), title: { en: 'Data downloads', zh: '数据下载' }, aliases: ['csv', 'json', 'xlsx', 'download', '下载'] },
    { id: 'page:methods', kind: 'page', href: localized('/about/'), title: { en: 'Methods, sources, and about GIDS', zh: '方法、来源与关于 GIDS' }, aliases: ['methods', 'sources', 'limitations', '方法', '来源', '局限'] },
    { id: 'page:copyright', kind: 'page', href: localized('/copyright/'), title: { en: 'Copyright, data licensing, and reuse', zh: '版权、数据许可与复用' }, aliases: ['copyright', 'licence', 'license', 'reuse', 'attribution', '版权', '许可', '复用', '署名'] },
  );

  return new Response(JSON.stringify({ schema_version: '1.0', generated_at: new Date().toISOString(), entries }), {
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'public, max-age=0, must-revalidate' },
  });
};
