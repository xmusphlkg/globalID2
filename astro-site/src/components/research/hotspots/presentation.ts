import { type HotspotPeriod, type AlluvialPeriod } from './types.ts';

export function escapeHtml(value: unknown) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

export function percentPoints(value: number | undefined) {
  const points = Math.round((value ?? 0) * 100);
  return `${points >= 0 ? '+' : ''}${points} pp`;
}

export function periodDate(period: HotspotPeriod) {
  return period.start_date ?? `${period.period}-01`;
}

export function periodShort(value: string) {
  return value.replace(/^(\d{4})-Q(\d)$/, 'Q$2 $1').replace(/^(\d{4})-(\d{2})$/, '$1-$2');
}

export function periodDisplay(value: string, periods: AlluvialPeriod[]) {
  const period = periods.find((item) => item.period === value);
  return period?.label ?? periodShort(value);
}

import { localizedDiseaseName } from '../../../utils/diseaseNames';
import { localizedResearchTopic } from '../../../utils/i18n';
import type { HeatmapRow } from './types.ts';

export function hotspotColors(theme: 'light' | 'dark') {
    const dark = theme === 'dark';
    return {
      text: dark ? '#f3f6fb' : '#162232',
      muted: dark ? '#a4b1c1' : '#556070',
      faint: dark ? '#7e91a6' : '#7a8ea0',
      line: dark ? '#344a64' : '#d9d2c7',
      surface: dark ? '#122132' : '#f7f3ec',
      popover: dark ? '#17283a' : '#fffdfa',
      palette: dark
        ? ['#76b7b2', '#e09976', '#7aa6c4', '#f0c16a', '#a48cf2', '#75c6ee', '#ef7878', '#9ed661']
        : ['#2a8f87', '#e06e4e', '#215f7c', '#d99121', '#7c5fd6', '#0e88b9', '#ca4b4b', '#70991e'],
    };
  }

export function hotspotLabels(lang: 'en' | 'zh' | 'fr') {
const t = (en: string, zh: string, fr: string) => (lang === 'zh' ? zh : lang === 'fr' ? fr : en);
const topicLabel = (value: string) => localizedResearchTopic(lang, value);
const diseaseLabel = (row: HeatmapRow) => localizedDiseaseName({
    // Heatmap keys are composite `disease_id::topic` identities; only the
    // disease identifier belongs in the canonical disease-name resolver.
    disease_id: row.key.split('::', 1)[0],
    name_en: row.disease_name_en,
    name_zh: row.disease_name_zh,
    name_fr: row.disease_name_fr,
  }, lang);
const periodLabel = (period: { period: string; label?: string; start_date?: string }) => {
    if (lang !== 'fr') return period.label ?? period.period;
    const quarter = period.period.match(/^(\d{4})-Q([1-4])$/);
    if (quarter) return `T${quarter[2]} ${quarter[1]}`;
    const dateValue = period.start_date ?? (/^\d{4}-\d{2}$/.test(period.period) ? `${period.period}-01` : '');
    return dateValue
      ? new Intl.DateTimeFormat('fr', { month: 'short', year: '2-digit', timeZone: 'UTC' }).format(new Date(dateValue))
      : period.period;
  };
return {t,topicLabel,diseaseLabel,periodLabel};
}
