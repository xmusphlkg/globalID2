import { useSurveillanceSummary } from '../public/useSurveillanceSummary';
import { countryLabel, cadence, number, pick, type Summary } from '../../lib/surveillance-presentation';
import { latestSnapshot } from '../../lib/surveillance-snapshot';
import type { Locale, SituationAlertRecord } from '../../utils/situationTypes';

export default function NoticeSnapshots({ alert, contextCodes, locale }: { alert: SituationAlertRecord; contextCodes: string[]; locale: Locale }) {
  const { data, version } = useSurveillanceSummary(alert.disease_id && /^D\d+$/i.test(alert.disease_id) ? `/site-data/diseases/${alert.disease_id.toLowerCase()}-summary.json` : undefined);
  if (!data) return null;
  const rows: Summary[] = data.reporting_countries ?? [];
  const recent = [...rows].sort((a, b) => String(b.dates_tail?.at(-1) ?? '').localeCompare(String(a.dates_tail?.at(-1) ?? ''))).slice(0, 3).map(row => row.code);
  const codes = [...new Set([alert.country_code, ...(contextCodes.length ? contextCodes : recent)].map(code => code.toUpperCase()))];
  const badges = codes.flatMap(code => {
    const row = rows.find(row => row.code.toUpperCase() === code);
    const snapshot = row && latestSnapshot(row);
    if (!snapshot || !row) return [];
    if (code !== alert.country_code.toUpperCase() && Date.now() - Date.parse(snapshot.date) > 730 * 86400000) return [];
    return [{ row, ...snapshot }];
  }).slice(0, 3);
  if (!badges.length) return null;
  return <div className="intl-panel-snapshot" data-surveillance-version={version}>
    {badges.map(({ row, date, value, change }) => <span key={row.code} className="intl-snapshot-badge">{pick(locale, 'Local snapshot', '本地快照', 'Instantané local')} · {countryLabel(row, locale)} · {date} · {number(value, locale)} · {cadence(row.period_granularity, locale)}{change !== null && ` · ${change > 0 ? '+' : ''}${change.toLocaleString(locale, { maximumFractionDigits: 1 })}% ${pick(locale, 'vs previous period', '较上一周期', 'vs période précédente')}`}</span>)}
    <small>{pick(locale, 'Local surveillance and outbreak notices retain their own dates and definitions.', '本地监测与疫情通报保留各自的日期和统计定义。', 'La surveillance locale et les alertes conservent leurs propres dates et définitions.')}</small>
  </div>;
}
