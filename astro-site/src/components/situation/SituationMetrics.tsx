import { useSituationData } from './useSituationData';
import type { Locale, SituationMapDataFile } from '../../utils/situationTypes';

export default function SituationMetrics({ locale, mode = 'metrics' }: { locale: Locale; mode?: 'date' | 'metrics' }) {
  const { data, version, error } = useSituationData<SituationMapDataFile>('/situation/map-data.json');
  const pick = (en: string, zh: string, fr: string) => locale === 'zh' ? zh : locale === 'fr' ? fr : en;
  if (mode === 'date') return <span data-situation-version={version}>{data?.as_of ?? (error ? pick('Unavailable', '暂不可用', 'Indisponible') : '…')}</span>;
  return <dl className="grid grid-cols-3 gap-5" data-situation-version={version}>
    {[[pick('Places with updates', '有更新的地点', 'Lieux avec des nouvelles'), data?.points?.length], [pick('Active outbreaks', '活跃疫情', 'Flambées actives'), data?.active_outbreaks], [pick('Contributing sources', '贡献来源', 'Sources contributrices'), data?.sources_ok]].map(([label, value]) => <div key={String(label)}><dt className="text-xs text-[rgb(var(--text-muted))]">{label}</dt><dd className="mt-2 text-2xl tabular-nums">{typeof value === 'number' ? value.toLocaleString(locale) : '—'}</dd></div>)}
  </dl>;
}
