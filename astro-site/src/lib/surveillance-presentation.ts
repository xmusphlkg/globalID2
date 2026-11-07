import { localizedDiseaseName } from '../utils/diseaseNames.ts';
import { localizedRegionName } from '../utils/i18n.ts';

export type Language = 'en' | 'zh' | 'fr';
export type Summary = Record<string, any>;
export const pick = (language: Language, en: string, zh: string, fr: string) => language === 'zh' ? zh : language === 'fr' ? fr : en;
export const number = (value: unknown, language: Language) => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString(language) : '—';
export const coverage = (value: Summary) => [value.date_range?.start, value.date_range?.end].filter(Boolean).join(' → ') || '—';
export const countryLabel = (row: Summary, language: Language) => localizedRegionName(language, String(row.code ?? row.country_code ?? ''), {
  en: row.name_en || row.country_name_en || row.name || row.country_name,
  zh: row.name_zh || row.country_name_zh, fr: row.name_fr || row.country_name_fr,
});
export const diseaseLabel = (row: Summary, language: Language) => localizedDiseaseName(row, language);
export function safeUrl(raw: unknown): string | undefined {
  if (typeof raw !== 'string' || !raw.trim()) return;
  try { return ['http:', 'https:'].includes(new URL(raw, 'https://local.invalid').protocol) ? raw : undefined; } catch { return; }
}
export function cadence(value: unknown, language: Language) {
  const key = String(value ?? '').toLowerCase().replaceAll(' ', '_').replaceAll('-', '_');
  const labels: Record<string, [string, string, string]> = {
    daily: ['Daily', '每日', 'Quotidienne'], weekly: ['Weekly', '每周', 'Hebdomadaire'],
    monthly: ['Monthly', '每月', 'Mensuelle'], quarterly: ['Quarterly', '每季度', 'Trimestrielle'],
    annual: ['Annual', '每年', 'Annuelle'], yearly: ['Annual', '每年', 'Annuelle'],
    weekdays: ['Weekdays', '工作日', 'Jours ouvrés'], periodic: ['Periodic', '定期', 'Périodique'],
    mixed: ['Mixed frequency', '混合频率', 'Fréquence mixte'], current_aggregate: ['Current aggregate', '当前汇总', 'Agrégat actuel'],
    to_be_confirmed: ['To be confirmed', '待确认', 'À confirmer'],
    mixed_weekly_annual: ['Weekly and annual', '周报与年报', 'Hebdomadaire et annuelle'],
    weekly_and_annual: ['Weekly and annual', '周报与年报', 'Hebdomadaire et annuelle'],
    annual_and_monthly: ['Annual and monthly', '年度与月度', 'Annuelle et mensuelle'],
    'monthly_facts_·_quarterly_publication': ['Monthly facts, quarterly publication', '月度事实，季度发布', 'Données mensuelles, publication trimestrielle'],
  };
  return labels[key]?.[language === 'zh' ? 1 : language === 'fr' ? 2 : 0] ?? String(value || '—');
}

/** Keep one entry per authority endpoint/cadence/definition, retaining its jurisdictions. */
export function sourceEntries(info: unknown): Summary[] {
  const groups = Array.isArray(info) ? info : info ? [info] : [];
  const unique = new Map<string, Summary>();
  for (const group of groups) {
    const sources = group.sources?.length ? group.sources : group.primary_url ? [{ label: group.primary_label, url: group.primary_url }] : [];
    for (const source of sources) {
      const label = source.label || source.scope || group.primary_label;
      const url = safeUrl(source.url);
      if (!label && !url) continue;
      const key = JSON.stringify([url, label, source.cadence, source.description]);
      const row = unique.get(key) ?? { ...source, label, url, countries: [] };
      const code = source.country_code || group.country_code;
      if (code && !row.countries.some((entry: Summary) => entry.code === code)) row.countries.push({ code, name_en: source.country_name || group.country_name });
      unique.set(key, row);
    }
  }
  return [...unique.values()];
}
