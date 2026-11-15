import { toSeoSlug } from '../../../lib/seo';
import { localizedDiseaseName } from '../../../utils/diseaseNames';
import { localizedRegionName } from '../../../utils/i18n';
import { formatFileSize, downloadPartRecordCount } from '../../../lib/download-presentation';
import { diseaseSearchAliases } from '../../../lib/disease-search';

export function downloadCatalogue(downloads: any, metaRaw: any, locale: 'en' | 'zh' | 'fr') {
const zh = locale === 'zh';
const fr = locale === 'fr';
const prefix = zh ? '/zh' : fr ? '/fr' : '';
const countryMetaByCode = new Map(
  ((metaRaw as any).countries ?? []).map((entry: any) => [String(entry.code).toUpperCase(), entry]),
);

const downloadableCountries = (downloads.countries ?? [])
  .filter(
    (entry: any) =>
      Number(entry.record_count ?? 0) > 0 &&
      entry.parts?.some((part: any) =>
        Object.values(part.files ?? {}).some((file: any) => Boolean((file as any)?.url)),
      ),
  );
const subdivisionCount = downloadableCountries.filter((entry: any) => {
  const metadata = countryMetaByCode.get(String(entry.code).toUpperCase()) as any;
  return metadata?.location_type === 'subdivision' || Boolean(metadata?.parent_code);
}).length;
const countries = downloadableCountries
  .filter((entry: any) => {
    const metadata = countryMetaByCode.get(String(entry.code).toUpperCase()) as any;
    return metadata?.location_type !== 'subdivision' && !metadata?.parent_code;
  })
  .map((entry: any) => {
    const part = entry.parts?.find((item: any) => item.is_current) ?? entry.parts?.at(-1);
    const partsCount = entry.parts?.length ?? 0;
    const recordCount = Number(entry.record_count ?? 0);
    const currentCount = downloadPartRecordCount(entry).current;
    return {
      kind: 'country',
      id: entry.code,
      name: zh
        ? localizedRegionName('zh', entry.code, { zh: entry.name_zh, en: entry.name_en ?? entry.name })
        : fr
          ? entry.name_fr ?? localizedRegionName('fr', entry.code, { en: entry.name_en ?? entry.name })
          : entry.name_en ?? entry.name ?? entry.code,
      aliases: `${entry.name_en ?? ''} ${entry.name_zh ?? ''} ${entry.name_fr ?? ''}`,
      detail: zh
        ? `当前文件 ${currentCount.toLocaleString('zh-CN')} 条 · 全部历史 ${recordCount.toLocaleString('zh-CN')} 条（${partsCount} 个时间范围）`
        : `${currentCount.toLocaleString(fr ? 'fr-FR' : 'en-US')} ${fr ? 'lignes dans ce fichier' : 'rows in this file'} · ${recordCount.toLocaleString(fr ? 'fr-FR' : 'en-US')} ${fr ? 'lignes au total' : 'rows across all periods'}`,
      files: part?.files ?? {},
      period: part?.label ?? (zh ? '最新可用' : fr ? 'Dernière période disponible' : 'Latest available'),
      pageHref: `${prefix}/countries/${String(entry.code).toLowerCase()}/#downloads`,
      count: recordCount,
    };
  });

const diseases = (downloads.diseases ?? []).map((entry: any) => {
  const part = entry.parts?.find((item: any) => item.is_current) ?? entry.parts?.at(-1);
  const countryCount = entry.country_count ?? entry.countries?.length ?? 0;
  const recordCount = Number(entry.record_count ?? 0);
    const currentCount = downloadPartRecordCount(entry).current;
  return {
    kind: 'disease',
    id: entry.disease_id ?? entry.id,
    name: zh
      ? entry.name_zh ?? entry.name_en ?? entry.slug
      : fr
        ? localizedDiseaseName({ ...entry, disease_id: entry.disease_id ?? entry.id }, 'fr')
        : entry.name_en ?? entry.slug ?? entry.id,
    aliases: diseaseSearchAliases(entry).join(' '),
    detail: zh
      ? `当前文件 ${currentCount.toLocaleString('zh-CN')} 条 · 全部历史 ${recordCount.toLocaleString('zh-CN')} 条 · ${countryCount} 个区域`
      : `${currentCount.toLocaleString(fr ? 'fr-FR' : 'en-US')} ${fr ? 'lignes dans ce fichier' : 'rows in this file'} · ${recordCount.toLocaleString(fr ? 'fr-FR' : 'en-US')} ${fr ? 'lignes au total' : 'rows across all periods'} · ${countryCount} ${fr ? 'zones' : 'areas'}`,
    files: part?.files ?? {},
    period: part?.label ?? (zh ? '最新可用' : fr ? 'Dernière période disponible' : 'Latest available'),
    pageHref: `${prefix}/diseases/${toSeoSlug(entry.slug)}/#downloads`,
    count: recordCount,
  };
});

const entries = [...countries, ...diseases];
const generatedAt = downloads.generated_at
  ? new Date(downloads.generated_at).toLocaleDateString(zh ? 'zh-CN' : fr ? 'fr-FR' : 'en-US', {
      year: 'numeric',
      month: zh ? 'numeric' : 'long',
      day: 'numeric',
      timeZone: 'UTC',
    })
    : zh
    ? '暂无'
    : fr
      ? 'Non disponible'
      : 'Not available';
const totalRecords = entries.reduce((sum: number, entry: any) => sum + entry.count, 0);
const compact = (value: number) =>
  new Intl.NumberFormat(zh ? 'zh-CN' : fr ? 'fr-FR' : 'en-US', {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value);
const bytes = formatFileSize;

return { countries, diseases, entries, generatedAt, totalRecords, compact, bytes, subdivisionCount, prefix };
}
