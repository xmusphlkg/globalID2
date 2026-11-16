import type { MapPoint, MonitoringProduct, SiteIndex } from '../../utils/situationTypes.ts';
import { ageDays, type Filters } from './model.ts';

const normalize = (value: string) => value.normalize('NFKC').toLocaleLowerCase('en').replace(/[-–—]/g, ' ').replace(/\s+/g, ' ').trim();
const escapeRegex = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

function titleMatcher(aliases: Array<string | null | undefined>): (title: string) => boolean {
  const names = [...new Set(aliases.filter((name): name is string => Boolean(name?.trim())).map(normalize))];
  const patterns = names.map(name => new RegExp(
    /\p{Script=Han}/u.test(name) ? escapeRegex(name) : `(?:^|[^\\p{L}\\p{N}])${escapeRegex(name)}(?=$|[^\\p{L}\\p{N}])`, 'u',
  ));
  return title => patterns.some(pattern => pattern.test(normalize(title)));
}

/**
 * Source and date always follow the map filters. Legacy monitoring records have
 * no extracted geography or disease, so only a named match in the title counts
 * as coverage; a regional publisher does not imply coverage of every country.
 */
export function filterMonitoring(
  products: MonitoringProduct[], filters: Filters, asOf: string, site: SiteIndex, points: MapPoint[],
): MonitoringProduct[] {
  // A roundup may cover several hosts; it has no per-event host contract.
  // Do not present it as a match for any explicit host filter.
  if (filters.host) return [];
  const disease = site.diseases[filters.disease];
  const country = site.countries[filters.country];
  const diseasePoints = filters.disease ? points.filter(point => (point.disease_id ?? point.disease_label) === filters.disease) : [];
  const countryPoints = filters.country ? points.filter(point => point.country_code === filters.country) : [];
  const diseaseMatches = titleMatcher([
    disease?.name_en, disease?.name_zh, disease?.slug.replaceAll('-', ' '),
    ...diseasePoints.flatMap(point => [point.disease_name_en, point.disease_name_zh, point.disease_label]),
    !disease && !diseasePoints.length && !/^D\d+$/i.test(filters.disease) ? filters.disease : null,
  ]);
  const countryMatches = titleMatcher([
    country?.name_en, country?.name_zh,
    ...countryPoints.flatMap(point => [point.country_name_en, point.country_name_zh]),
  ]);
  const limit = filters.window === 'all' ? Infinity : Number(filters.window);
  const seen = new Set<string>();
  return [...products].sort((a, b) => b.published_date.localeCompare(a.published_date) || a.notice_id.localeCompare(b.notice_id)).filter(product => {
    if (filters.source && product.source_id !== filters.source) return false;
    const age = ageDays(product.published_date, asOf);
    if (!Number.isFinite(age) || age < 0 || age > limit) return false;
    if (filters.disease && !(product.disease_ids
      ? product.disease_ids.includes(filters.disease)
      : diseaseMatches(product.title_en ?? ''))) return false;
    if (filters.country && !(product.country_codes
      ? product.country_codes.includes(filters.country)
      : countryMatches(product.title_en ?? ''))) return false;
    if (seen.has(product.notice_id)) return false;
    seen.add(product.notice_id);
    return true;
  });
}
