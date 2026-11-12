import { useSurveillanceSummary } from '../public/useSurveillanceSummary';
import { coverage as datasetCoverage, pick, sourceEntries, type Language } from '../../lib/surveillance-presentation.ts';
import { chartSourceScope, scopedChartSources } from '../../lib/chart-source-scope.ts';
import type { AnalysisResult, Catalog } from './surveillance/domain/types.ts';

export default function RuntimeChartSource({ dataUrl, language, catalog, result }: { dataUrl?: string; language: Language; catalog?: Catalog; result?: AnalysisResult }) {
  const summaryUrl = dataUrl?.replace(/\.json$/, '-summary.json');
  const { data, version } = useSurveillanceSummary(summaryUrl);
  if (!data) return null;
  const scope = catalog && result ? chartSourceScope(catalog, result) : null;
  if (scope && !scope.geographies.length) return null;
  const sources = scope ? scopedChartSources(data.source_info, scope.geographies, scope.provenance) : sourceEntries(data.source_info);
  const coverage = scope ? scope.dates.length ? `${scope.dates[0]} → ${scope.dates.at(-1)}` : '—' : datasetCoverage(data);
  const sourceLinks = sources.map((source, index) => <span key={`${source.label}:${index}`}>{index > 0 && ' · '}{source.url ? <a className="underline" href={source.url} target="_blank" rel="noopener noreferrer">{source.label}</a> : source.label}</span>);
  return <div className="figure-caption mt-3" data-surveillance-version={version} data-source-label={sources.map(source => source.label).join('; ')} data-source-coverage={coverage} data-source-updated={data.generated_at}>
    <p>{scope ? pick(language, 'Sources for selected series', '所选曲线来源', 'Sources des séries sélectionnées') : pick(language, 'Dataset sources', '数据集来源', 'Sources du jeu de données')}: {sources.length ? sourceLinks.slice(0, 2) : pick(language, 'See the source details for each series.', '请查看各曲线的来源详情。', 'Consultez les sources de chaque série.')}{sources.length > 2 && ` +${sources.length - 2}`} · {scope ? pick(language, 'Displayed observations', '图示观测日期', 'Dates des observations affichées') : pick(language, 'Dataset coverage', '数据集时间范围', 'Période du jeu de données')}: {coverage} · {pick(language, 'Data snapshot', '数据快照', 'Version des données')} {String(data.generated_at ?? '').slice(0, 10)}</p>
    {sources.length > 2 && <details className="mt-2"><summary>{pick(language, 'View all selected sources', '查看所选曲线的全部来源', 'Voir toutes les sources sélectionnées')}</summary><p className="mt-2">{sourceLinks}</p></details>}
  </div>;
}
