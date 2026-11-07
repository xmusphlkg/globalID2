import { useState } from 'react';
import { cadence, countryLabel, pick, sourceEntries, type Language, type Summary } from '../../lib/surveillance-presentation.ts';

export default function SurveillanceSources({ data, language }: { data: Summary; language: Language }) {
  const [expanded, setExpanded] = useState(false);
  const sources = sourceEntries(data.source_info);
  if (!sources.length) return <p className="figure-caption">{pick(language, 'Source details are recorded with the published series.', '来源详情随已发布序列提供。', 'Les sources accompagnent les séries publiées.')}</p>;
  return <div className="space-y-3" data-testid="surveillance-sources">
    <div className="grid gap-3 sm:grid-cols-2">{(expanded ? sources : sources.slice(0, 8)).map((source, index) => <article className="border border-[rgb(var(--border))] p-4 text-sm" key={index}>
      <h3 className="font-semibold">{source.url ? <a className="underline" href={source.url} target="_blank" rel="noopener noreferrer">{source.label}</a> : source.label}</h3>
      {!!source.countries.length && <p className="mt-1 text-xs text-[rgb(var(--text-muted))]">{source.countries.map((row: Summary) => countryLabel(row, language)).join(' · ')}</p>}
      {source.cadence && <p className="mt-2">{pick(language, 'Reporting cadence', '报告频率', 'Fréquence de déclaration')}: {cadence(source.cadence, language)}</p>}
      {source.description && <p className="mt-2 text-xs leading-5 text-[rgb(var(--text-muted))]">{source[`description_${language}`] || source.description}</p>}
    </article>)}</div>
    {sources.length > 8 && <button type="button" className="site-action-secondary border px-3 py-2 text-sm" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? pick(language, 'Show fewer sources', '收起来源', 'Réduire les sources') : `${pick(language, 'All sources', '全部来源', 'Toutes les sources')} (${sources.length})`}</button>}
  </div>;
}
