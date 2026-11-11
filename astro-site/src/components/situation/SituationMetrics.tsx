import { useSituationData } from './useSituationData';
import type { Locale, SituationMapDataFile } from '../../utils/situationTypes';
import { visibleSituationPoints } from './model';
import GIDSLoader from '../GIDSLoader';

export default function SituationMetrics({ locale, mode = 'metrics' }: { locale: Locale; mode?: 'date' | 'metrics' }) {
  const { data, version, error, retry } = useSituationData<SituationMapDataFile>('/situation/map-data.json');
  const pick = (en: string, zh: string, fr: string) => locale === 'zh' ? zh : locale === 'fr' ? fr : en;
  if (mode === 'date') return <span data-situation-version={version}>{data?.as_of ?? (error ? pick('Unavailable', '暂不可用', 'Indisponible') : '…')}</span>;
  if (!data) return error ? <p role="status">{pick('Situation data is unavailable.', '暂时无法读取态势数据。', 'Les données de situation sont indisponibles.')} <button type="button" className="underline" onClick={retry}>{pick('Retry', '重试', 'Réessayer')}</button></p> : <GIDSLoader language={locale} compact />;
  const recentPlaces = visibleSituationPoints(data.points ?? [], data.as_of).length;
  return <dl className="grid grid-cols-3 gap-5" data-situation-version={version}>
    {[[pick('Places updated · last 30 days', '近 30 天有更新的地点', 'Lieux actualisés · 30 jours'), recentPlaces], [pick('Active outbreaks', '活跃疫情', 'Flambées actives'), data.active_outbreaks], [pick('Contributing sources', '提供通报的来源', 'Sources contributrices'), data.sources_ok]].map(([label, value]) => <div key={String(label)}><dt className="text-xs" style={{ color: 'inherit', opacity: 0.9 }}>{label}</dt><dd className="mt-2 text-2xl tabular-nums">{typeof value === 'number' ? value.toLocaleString(locale) : '—'}</dd></div>)}
  </dl>;
}
