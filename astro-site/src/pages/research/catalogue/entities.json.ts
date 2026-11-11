import routes from '../../../data/research/routes.json';
import { diseaseSearchAliases } from '../../../lib/disease-search';
import { localizedRegionName, localizedResearchTopic } from '../../../utils/i18n';

export function GET() {
  const facets = (routes as any).facets ?? {};
  const entities = [
    ...(facets.diseases ?? []).map((row: any) => ({ scope: 'disease', value: String(row.disease_id).toLowerCase(), aliases: diseaseSearchAliases(row), path: `/site-data/research/diseases/${String(row.disease_id).toLowerCase()}.json` })),
    ...(facets.countries ?? []).map((row: any) => ({ scope: 'country', value: String(row.code).toLowerCase(), aliases: [row.name_en, row.name_zh, row.name_fr, localizedRegionName('zh', row.code, { en: row.name_en, zh: row.name_zh }), localizedRegionName('fr', row.code, { en: row.name_en, fr: row.name_fr })].filter(Boolean), path: `/site-data/research/countries/${row.slug}.json` })),
    ...(facets.topics ?? []).map((row: any) => ({ scope: 'topic', value: row.slug, aliases: [row.name, localizedResearchTopic('zh', row.name), localizedResearchTopic('fr', row.name)].filter(Boolean), path: `/site-data/research/topics/${row.slug}.json` })),
  ];
  return new Response(JSON.stringify({ schema_version: 1, entities }), { headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-cache' } });
}
