/** Localised labels and number/date formatting shared by the server pages and the map island. */

import type { AlertStatus, CountBasis, HostMetadata, HostType, Locale, SituationAlertRecord } from '../../utils/situationTypes.ts';

const TAG: Record<Locale, string> = { en: 'en-US', zh: 'zh-CN', fr: 'fr-FR' };

type Text3 = Record<Locale, string>;
const t = (en: string, zh: string, fr: string): Text3 => ({ en, zh, fr });

export function formatDay(value: string | null | undefined, locale: Locale): string {
  if (!value) return '';
  const date = new Date(`${value.slice(0, 10)}T00:00:00Z`);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(TAG[locale], { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' }).format(date);
}

export function formatCount(value: number, locale: Locale): string {
  return value.toLocaleString(TAG[locale]);
}

export function formatSignedPercent(value: number, locale: Locale): string {
  const digits = Math.abs(value) < 10 ? 1 : 0;
  const formatted = value.toLocaleString(TAG[locale], {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
    signDisplay: 'exceptZero',
  });
  return `${formatted}%`;
}

const regionNames = new Map<Locale, Intl.DisplayNames | null>();

function regionName(code: string, locale: Locale): string | null {
  if (!regionNames.has(locale)) {
    try {
      regionNames.set(locale, new Intl.DisplayNames([TAG[locale]], { type: 'region' }));
    } catch {
      regionNames.set(locale, null);
    }
  }
  try {
    const name = regionNames.get(locale)?.of(code.toUpperCase());
    return name && name !== code.toUpperCase() ? name : null;
  } catch {
    return null;
  }
}

type DiseaseNames = {
  disease_label: string;
  disease_name_en: string;
  disease_name_zh?: string | null;
};
type CountryNames = {
  country_code: string;
  country_name_en: string;
  country_name_zh?: string | null;
};
type SourceNames = {
  source_id: string;
  source_label_en?: string | null;
  source_label_zh?: string | null;
};

export function diseaseName(record: DiseaseNames, locale: Locale): string {
  const zh = locale === 'zh' ? record.disease_name_zh : null;
  return zh || record.disease_name_en || record.disease_label;
}

export function countryName(record: CountryNames, locale: Locale): string {
  if (locale === 'zh' && record.country_name_zh) return record.country_name_zh;
  if (locale === 'fr') return regionName(record.country_code, 'fr') || record.country_name_en || record.country_code;
  return record.country_name_en || record.country_code;
}

export function sourceLabel(record: SourceNames, locale: Locale): string {
  return (locale === 'zh' && record.source_label_zh) || record.source_label_en || record.source_id;
}

export function alertSummary(record: SituationAlertRecord, locale: Locale): string {
  if (locale === 'zh') return record.summary_zh || record.summary_en || '';
  if (locale === 'fr') return record.summary_fr || record.summary_en || '';
  return record.summary_en || '';
}

export const STATUS_LABELS: Record<AlertStatus, Text3> = {
  new: t('New', '新发', 'Nouveau'),
  ongoing: t('Ongoing', '持续', 'En cours'),
  imported: t('Imported', '输入', 'Importé'),
  ended: t('Ended', '结束', 'Terminé'),
  routine: t('Routine', '常规监测', 'Surveillance courante'),
};

const BASIS_LABELS: Record<CountBasis, Text3> = {
  confirmed: t('confirmed', '确诊', 'confirmés'),
  probable: t('probable', '可能', 'probables'),
  suspected: t('suspected', '疑似', 'suspects'),
  total: t('total', '合计', 'total'),
};

export function basisLabel(basis: CountBasis, locale: Locale): string {
  return BASIS_LABELS[basis][locale];
}

export const HOST_LABELS: Record<HostType, Text3> = {
  human: t('Human', '人类', 'Humain'),
  animal: t('Animal', '动物', 'Animal'),
  unknown: t('Host unspecified', '宿主未明确', 'Hôte non précisé'),
};

export const HOST_FILTER_LABEL = t('Affected host', '受影响宿主', 'Hôte touché');

export function hostLabel(record: HostMetadata, locale: Locale): string {
  const type = record.host_type === 'human' || record.host_type === 'animal' ? record.host_type : 'unknown';
  const species = type === 'animal' && record.host_species ? ` · ${record.host_species}` : '';
  return `${HOST_LABELS[type][locale]}${species}`;
}

/** Only an explicit, matching unit permits a human/animal count label. */
export function countLabels(record: HostMetadata, locale: Locale): { cases: string; deaths: string } {
  if (record.host_type === 'animal' && record.count_unit === 'animals') {
    return { cases: t('affected animals', '只患病动物', 'animaux atteints')[locale], deaths: t('animal deaths', '只动物死亡', 'animaux morts')[locale] };
  }
  if (record.host_type === 'human' && record.count_unit === 'people') {
    return { cases: t('human cases', '例人类病例', 'cas humains')[locale], deaths: t('human deaths', '例人类死亡', 'décès humains')[locale] };
  }
  return { cases: t('reported cases (unit unspecified)', '报告病例（计数单位未明确）', 'cas signalés (unité non précisée)')[locale], deaths: t('reported deaths (unit unspecified)', '报告死亡（计数单位未明确）', 'décès signalés (unité non précisée)')[locale] };
}

/** Counts exactly as the report states them: "9,704 cases (confirmed) · 4,687 deaths · as of 20 Oct 2026". */
export function countsLine(record: SituationAlertRecord, locale: Locale): string {
  const parts: string[] = [];
  const basis = record.count_basis ? ` (${BASIS_LABELS[record.count_basis][locale]})` : '';
  const { cases: caseWord, deaths: deathWord } = countLabels(record, locale);
  if (record.cases != null) {
    parts.push(locale === 'zh' ? `${formatCount(record.cases, locale)} ${caseWord}${basis}` : `${formatCount(record.cases, locale)} ${caseWord}${basis}`);
  }
  if (record.deaths != null) parts.push(`${formatCount(record.deaths, locale)} ${deathWord}`);
  if (parts.length && record.as_of) parts.push(`${t('as of', '截至', 'au')[locale]} ${formatDay(record.as_of, locale)}`);
  return parts.join(' · ');
}
