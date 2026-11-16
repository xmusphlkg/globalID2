/** Offer entity feeds represented in the retained Situation filter catalogue. */
import { localizedDiseaseName, type DiseaseNameRecord } from '../utils/diseaseNames.ts';
import { localizedRegionName, type Lang } from '../utils/i18n.ts';

interface Facet {
  id: string;
  count: number;
  [field: string]: unknown;
}
interface FeedFacets { facets?: { countries?: Facet[]; diseases?: Facet[] } }

const text = (value: unknown) => typeof value === 'string' ? value : undefined;

export function buildSituationRssOptions(mapping: FeedFacets | null, catalogue: DiseaseNameRecord[], locale: Lang) {
  const countries = (mapping?.facets?.countries ?? []).filter(row => (
    /^[A-Z]{2}(?:-[A-Z0-9]{1,8})?$/.test(row.id) && row.count > 0
  )).map(row => ({ code: row.id, name: localizedRegionName(locale, row.id, {
    en: text(row.country_name_en), zh: text(row.country_name_zh), fr: text(row.country_name_fr),
  }) })).sort((a, b) => a.name.localeCompare(b.name, locale));
  const byId = new Map(catalogue.map(row => [row.disease_id, row]));
  const diseases = (mapping?.facets?.diseases ?? []).filter(row => (
    /^D\d{3,6}$/.test(row.id) && row.count > 0 && byId.has(row.id)
  )).map(row => ({ id: row.id, name: localizedDiseaseName(byId.get(row.id)!, locale) }))
    .sort((a, b) => a.name.localeCompare(b.name, locale));
  return { countries, diseases };
}
