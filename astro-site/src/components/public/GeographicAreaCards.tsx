import type { Ref } from 'react';
import { getFlagAssetPath } from '../../lib/country-flag';
import { countryLabel, cadence, safeUrl, sourceEntries, type Language, type Summary } from '../../lib/surveillance-presentation';
import { hasData } from '../../lib/surveillance-coverage';

export default function GeographicAreaCards({ rows, language, gridRef }: { rows: Summary[]; language: Language; gridRef?: Ref<HTMLDivElement> }) {
  const pick = (en: string, zh: string, fr: string) => language === 'zh' ? zh : language === 'fr' ? fr : en;
  const prefix = language === 'en' ? '' : `/${language}`;
  return <div className="directory-grid" ref={gridRef}>
    {rows.map(row => {
      const active = hasData(row), name = countryLabel(row, language);
      const source = sourceEntries(row.source_info)[0];
      const sourceLabel = source?.label || row.source_name;
      const sourceCadence = source?.cadence || row.cadence;
      const sourceUrl = safeUrl(source?.url || row.source_url);
      const content = <>
        <div className="directory-card-top"><span className="directory-card-code"><img src={getFlagAssetPath(row.code)} alt="" width="28" height="21" loading="lazy" />{row.code}</span>
          <span className={`directory-card-status${active ? ' is-active' : ''}`}>{active ? pick('Supported', '已支持', 'Disponible') : row.publication_pending ? pick('Pending', '待发布', 'En attente') : row.status === 'Supported' ? pick('Unavailable', '暂不可用', 'Indisponible') : pick('Planned', '规划中', 'Prévu')}</span>
        </div>
        <h3>{name}</h3>
        {active ? <div className="directory-card-metrics"><p><strong>{Number(row.disease_count ?? 0).toLocaleString()}</strong> {pick('diseases', '种疾病', 'maladies')}</p>
          <p className="directory-card-dates">{[row.date_range?.start, row.date_range?.end].filter(Boolean).join(' → ') || pick('Coverage dates unavailable', '暂无覆盖时间', 'Période indisponible')}</p>
          {sourceLabel && <p className="directory-card-source" title={sourceLabel}>{sourceLabel}{sourceCadence ? ` · ${cadence(sourceCadence, language)}` : ''}</p>}
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12h14m-5-5 5 5-5 5" /></svg>
        </div> : <p className="directory-card-note">{row.publication_pending ? pick('Data publication pending', '数据待发布', 'Publication des données en attente') : row.status === 'Supported' ? pick('Data temporarily unavailable', '数据暂不可用', 'Données temporairement indisponibles') : pick('Planned source', '规划中的数据源', 'Source prévue')}</p>}
      </>;
      return active ? <a key={row.code} className="directory-card" href={`${prefix}/countries/${String(row.code).toLowerCase()}/`}>{content}</a> : <div key={row.code} className="directory-card is-unavailable">{content}
        {sourceCadence && <p className="directory-card-note">{pick('Reporting cadence', '报告频率', 'Fréquence de déclaration')}: {cadence(sourceCadence, language)}</p>}
        {sourceUrl && <a className="directory-source-link" href={sourceUrl} target="_blank" rel="noopener noreferrer">{pick('Official source', '官方数据源', 'Source officielle')}: {sourceLabel || sourceUrl}</a>}
      </div>;
    })}
  </div>;
}
