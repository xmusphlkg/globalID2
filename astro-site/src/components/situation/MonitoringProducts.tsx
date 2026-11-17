import { useMemo, useState } from 'react';
import type { Locale, MapPoint, MonitoringProduct, SiteIndex } from '../../utils/situationTypes.ts';
import type { Filters } from './model.ts';
import { COPY, formatCount, formatDay, sourceLabel } from './labels.ts';
import { filterMonitoring } from './monitoring.ts';
import './monitoring.css';

const PAGE_SIZE = 6;
const TEXT = {
  en: { empty: 'No monitoring products match these filters.', more: 'Show more products', title: 'Monitoring report' },
  zh: { empty: '没有符合筛选条件的监测产品。', more: '显示更多监测产品', title: '监测报告' },
  fr: { empty: 'Aucun produit de surveillance ne correspond à ces filtres.', more: 'Afficher plus de produits', title: 'Rapport de surveillance' },
};

export default function MonitoringProducts({ locale, monitoring, filters, asOf, site, points }: {
  locale: Locale; monitoring: MonitoringProduct[]; filters: Filters; asOf: string; site: SiteIndex; points: MapPoint[];
}) {
  const rows = useMemo(() => filterMonitoring(monitoring, filters, asOf, site, points), [monitoring, filters, asOf, site, points]);
  const scope = JSON.stringify(filters);
  const [page, setPage] = useState({ scope, limit: PAGE_SIZE });
  const limit = page.scope === scope ? page.limit : PAGE_SIZE;
  const text = TEXT[locale];
  return (
    <section className="sit-monitor-products" aria-labelledby="sit-monitor-heading">
      <div className="sit-monitor-heading">
        <h2 id="sit-monitor-heading">{COPY[locale].monitoringHeading}</h2>
        <span className="sit-monitor-count" aria-live="polite">{formatCount(rows.length, locale)}</span>
      </div>
      <p className="sit-muted">{COPY[locale].monitoringLead}</p>
      {rows.length === 0 ? <p className="sit-muted" role="status">{text.empty}</p> : (
        <ul className="sit-monitor-cards">
          {rows.slice(0, limit).map(product => (
            <li key={product.notice_id} className="sit-monitor-card">
              <div className="sit-monitor-meta">
                <span>{sourceLabel(product, locale)}</span>
                <time dateTime={product.published_date}>{formatDay(product.published_date, locale)}</time>
              </div>
              <a href={product.url} target="_blank" rel="noopener noreferrer">
                {product.title_en || text.title}<span aria-hidden="true"> ↗</span>
              </a>
            </li>
          ))}
        </ul>
      )}
      {rows.length > limit && (
        <button type="button" className="sit-text-btn sit-more" onClick={() => setPage({ scope, limit: limit + PAGE_SIZE })}>
          {text.more} ({formatCount(limit, locale)}/{formatCount(rows.length, locale)})
        </button>
      )}
    </section>
  );
}
