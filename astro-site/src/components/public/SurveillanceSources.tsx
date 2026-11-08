import { useState } from 'react';
import { getFlagAssetPath } from '../../lib/country-flag';
import './surveillance-sources.css';
import { cadence, countryLabel, pick, sourceEntries, type Language, type Summary } from '../../lib/surveillance-presentation.ts';

export default function SurveillanceSources({ data, language }: { data: Summary; language: Language }) {
  const [expanded, setExpanded] = useState(false);
  const sources = sourceEntries(data.source_info);
  if (!sources.length) return <p className="figure-caption">{pick(language, 'Source details are recorded with the published series.', '来源详情随已发布序列提供。', 'Les sources accompagnent les séries publiées.')}</p>;
  return <div className="surveillance-sources space-y-3" data-testid="surveillance-sources">
    <h3 className="surveillance-sources__heading">{pick(language, 'Official sources', '官方数据来源', 'Sources officielles')}</h3>
    <div className="grid gap-3 sm:grid-cols-2">{(expanded ? sources : sources.slice(0, 8)).map((source, index) => <article className="surveillance-source" key={index}>
      <h4 className="surveillance-source__title">{source.url ? <a className="underline" href={source.url} target="_blank" rel="noopener noreferrer">{source.label}</a> : source.label}</h4>
      {!!source.countries.length && <div className="surveillance-source__countries">{source.countries.map((row: Summary) => <span key={row.code}><img src={getFlagAssetPath(row.code)} alt="" width="32" height="24" loading="lazy" />{countryLabel(row, language)}</span>)}</div>}
      {source.cadence && <p className="mt-2">{pick(language, 'Reporting cadence', '报告频率', 'Fréquence de déclaration')}: {cadence(source.cadence, language)}</p>}
      {source.description && <p className="mt-2 text-xs leading-5 text-[rgb(var(--text-muted))]">{source[`description_${language}`] || source.description}</p>}
    </article>)}</div>
    {sources.length > 8 && <button type="button" className="site-action-secondary border px-3 py-2 text-sm" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? pick(language, 'Show fewer sources', '收起来源', 'Réduire les sources') : `${pick(language, 'All sources', '全部来源', 'Toutes les sources')} (${sources.length})`}</button>}
  </div>;
}
