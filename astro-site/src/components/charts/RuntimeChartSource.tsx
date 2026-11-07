import { useSurveillanceSummary } from '../public/useSurveillanceSummary';
import { coverage, pick, sourceEntries, type Language } from '../../lib/surveillance-presentation.ts';

export default function RuntimeChartSource({ dataUrl, language }: { dataUrl?: string; language: Language }) {
  const summaryUrl = dataUrl?.replace(/\.json$/, '-summary.json');
  const { data, version } = useSurveillanceSummary(summaryUrl);
  if (!data) return null;
  const sources = sourceEntries(data.source_info);
  return <p className="figure-caption mt-3" data-surveillance-version={version} data-source-label={sources.map(source => source.label).join('; ')} data-source-coverage={coverage(data)} data-source-updated={data.generated_at}>
    {pick(language, 'Sources', '来源', 'Sources')}: {sources.slice(0, 2).map((source, index) => <span key={index}>{index > 0 && ' · '}{source.url ? <a className="underline" href={source.url} target="_blank" rel="noopener noreferrer">{source.label}</a> : source.label}</span>)}{sources.length > 2 && ` +${sources.length - 2}`} · {coverage(data)} · {pick(language, 'Data updated', '数据更新于', 'Données mises à jour')} {String(data.generated_at ?? '').slice(0, 10)}
  </p>;
}
